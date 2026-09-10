import { useCallback, useEffect, useRef, useState } from 'react';
import {
  activeJob,
  api,
  ApiError,
  newKey,
  post,
  versionsForDate,
  type Input,
  type Job,
  type Result,
  type Schema,
  type Version,
} from './api';

export function useOperations(date: string) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [version, setVersion] = useState<Version | null>(null);
  const [snapshots, setSnapshots] = useState<Schema['OperationalDaySummaryResponse'][]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const locked = useRef(false);
  const pollEpoch = useRef(0);
  const submission = useRef<{ version: string; key: string } | null>(null);
  const jobStorage = version ? `ramp-job:${version.id}` : '';
  const reportError = (e: unknown) =>
    setError(
      e instanceof ApiError
        ? e
        : new ApiError(e instanceof Error ? e.message : 'The operation could not be completed.'),
    );

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const [allVersions, firstPage] = await Promise.all([
        versionsForDate(date, signal),
        api<Schema['OperationalDayListResponse']>(
          `/operational-days?operational_date=${date}&limit=100`,
          { signal },
        ),
      ]);
      const allSnapshots = [...firstPage.items];
      for (let offset = 100; offset < firstPage.total; offset += 100) {
        const page = await api<Schema['OperationalDayListResponse']>(
          `/operational-days?operational_date=${date}&limit=100&offset=${offset}`,
          { signal },
        );
        allSnapshots.push(...page.items);
      }
      if (signal?.aborted) return;
      setVersions(allVersions);
      setVersion(allVersions.at(-1) || null);
      setSnapshots(allSnapshots);
    },
    [date],
  );
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    reload(controller.signal)
      .catch((e) => {
        if (!controller.signal.aborted) reportError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reload]);

  const [tracked, setTracked] = useState<{ id: string; version: string } | null>(null);
  useEffect(() => {
    setJob(null);
    setResult(null);
    setPollError(null);
    let id: string | null = null;
    try {
      id = localStorage.getItem(jobStorage);
    } catch {
      /* Optional device-local recovery. */
    }
    setTracked(id && version ? { id, version: version.id } : null);
  }, [version?.id, jobStorage]);

  useEffect(() => {
    if (!tracked || tracked.version !== version?.id) return;
    const controller = new AbortController();
    const epoch = ++pollEpoch.current;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Job>(`/optimization-jobs/${tracked!.id}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted || epoch !== pollEpoch.current) return;
        if (next.operational_day_version_id !== tracked!.version)
          throw new ApiError('This job belongs to another input version.');
        setJob(next);
        setPollError(null);
        if (next.result_run_id || next.has_partial_result) {
          const output = await api<Result>(`/optimization-jobs/${next.id}/result`, {
            signal: controller.signal,
          });
          if (!controller.signal.aborted && epoch === pollEpoch.current) setResult(output);
        }
        if (activeJob(next.status)) timer = setTimeout(() => void poll(), 1500);
      } catch (e) {
        if (!controller.signal.aborted && epoch === pollEpoch.current) {
          setPollError(e instanceof Error ? e.message : 'Status unavailable.');
          timer = setTimeout(() => void poll(), 5000);
        }
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [tracked, version?.id, pollRevision]);

  async function mutate(work: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      reportError(e);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function loadSnapshot(id: string) {
    let loaded = false;
    await mutate(async () => {
      const created = await post<Version>(`/operational-days/${date}/drafts`, {
        ...(versions.some((v) => v.id === id)
          ? { source_version_id: id }
          : { source_snapshot_id: id }),
        idempotency_key: newKey(),
        reason: 'Loaded for supervisor review',
      });
      await reload();
      setVersion(created);
      loaded = true;
    });
    return loaded;
  }
  async function revise(input: Input, reason: string) {
    if (!version) return false;
    let saved = false;
    await mutate(async () => {
      const updated = await post<Version>(`/operational-day-versions/${version.id}/revisions`, {
        expected_parent_hash: version.content_hash,
        input,
        reason,
        idempotency_key: newKey(),
      });
      setVersions((prev) => [...prev, updated]);
      setVersion(updated);
      saved = true;
    });
    return saved;
  }
  async function optimize() {
    if (!version || activeJob(job?.status) || (tracked?.version === version.id && !job)) return;
    await mutate(async () => {
      if (submission.current?.version !== version.id)
        submission.current = { version: version.id, key: newKey() };
      const created = await post<Job>('/optimization-jobs', {
        operational_day_version_id: version.id,
        idempotency_key: submission.current.key,
      });
      submission.current = null;
      try {
        localStorage.setItem(jobStorage, created.id);
      } catch {
        /* Job is still usable during this session. */
      }
      setJob(created);
      setResult(null);
      // A job ID can also be restored without relying on device storage.
      setTracked({ id: created.id, version: version.id });
    });
  }
  async function cancel() {
    if (job)
      await mutate(async () => {
        ++pollEpoch.current;
        const next = await post<Job>(`/optimization-jobs/${job.id}/cancel`, {});
        setJob(next);
        setTracked({ id: next.id, version: version!.id });
      });
  }
  async function refresh() {
    await mutate(async () => {
      await reload();
      setPollRevision((n) => n + 1);
    });
  }
  async function restore(id: string) {
    await mutate(async () => {
      const restored = await api<Job>(`/optimization-jobs/${id}`);
      if (restored.operational_day_version_id !== version?.id)
        throw new ApiError('Select the input version used by this job before restoring it.');
      try {
        localStorage.setItem(jobStorage, id);
      } catch {
        /* optional */
      }
      setJob(restored);
      setTracked({ id: restored.id, version: version!.id });
    });
  }
  const currentJob = job?.operational_day_version_id === version?.id ? job : null;
  const currentResult = currentJob && result?.job_id === currentJob.id ? result : null;
  return {
    versions,
    version,
    selectVersion: setVersion,
    snapshots,
    loading,
    busy,
    error,
    job: currentJob,
    result: currentResult,
    pollError,
    recoveringJob: tracked?.version === version?.id && !!tracked && !currentJob,
    loadSnapshot,
    revise,
    optimize,
    cancel,
    refresh,
    restore,
  };
}
