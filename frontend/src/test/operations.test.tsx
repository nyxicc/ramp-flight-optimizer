// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fixture from './fixture.json';
import {
  api,
  ApiError,
  time,
  versionsForDate,
  type Input,
  type Job,
  type Result,
  type Version,
} from '../api';
import { useOperations } from '../useOperations';
import { JobPanel } from '../JobPanel';
import { Schedule, Workforce } from '../Schedule';
import { EditInput } from '../EditInput';

const version: Version = {
  id: 'version-1',
  operational_date: '2035-04-15',
  version_number: 1,
  parent_version_id: null,
  source_snapshot_id: null,
  created_at: '2035-04-15T00:00:00Z',
  content_hash: 'a'.repeat(64),
  input: fixture.input as Input,
  reason: null,
  warnings: [],
  valid: true,
};
const job: Job = {
  id: 'job-1',
  status: 'RUNNING',
  operational_day_id: version.id,
  operational_day_version_id: version.id,
  input_hash: version.content_hash,
  config: version.input.config!,
  timeout_seconds: 180,
  created_at: version.created_at,
  started_at: version.created_at,
  finished_at: null,
  progress: { phase: 'SOLVING', pass_number: 1, stage_number: 2, stage_name: 'minimum_staffing' },
  error_code: null,
  result_run_id: null,
  has_partial_result: false,
};
const output: Result = {
  job_id: job.id,
  status: 'SUCCEEDED',
  partial: false,
  result_run_id: 'run-1',
  result: fixture.result as Result['result'],
};
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
function mockService(
  extra?: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined,
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const path = String(url);
    const handled = extra?.(path, init);
    if (handled) return handled;
    if (path.includes('/versions?')) return response([version]);
    if (path.includes('/operational-days?')) return response({ items: [], total: 0 });
    if (path.endsWith('/result')) return response(output);
    if (path.includes('/optimization-jobs/')) return response(job);
    if (path.endsWith('/optimization-jobs')) return response(job, 202);
    throw new Error(`Unexpected request ${path}`);
  });
}
beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('operations API and lifecycle', () => {
  it('preserves airport clock values across browser timezones', () => {
    expect(time('2035-04-15T08:15:00-05:00')).toBe('08:15');
    expect(time(null)).toBe('—');
  });
  it('retains structured validation details', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response(
        {
          error: {
            message: 'Invalid shift',
            details: [{ code: 'SHIFT', path: 'employee_shifts[0]', message: 'End precedes start' }],
          },
        },
        422,
      ),
    );
    await expect(api('/bad')).rejects.toMatchObject({
      status: 422,
      details: [{ code: 'SHIFT', message: 'End precedes start' }],
    });
  });
  it('loads all pages of ascending input versions', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        response(Array.from({ length: 100 }, (_, i) => ({ ...version, version_number: i + 1 }))),
      )
      .mockResolvedValueOnce(response([{ ...version, version_number: 101 }]));
    expect((await versionsForDate(version.operational_date)).at(-1)?.version_number).toBe(101);
    expect(fetch.mock.calls[1][0]).toContain('offset=100');
  });
  it('submits only the immutable version ID, deduplicates clicks, and restores tracking', async () => {
    const fetch = mockService();
    const { result, unmount } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await Promise.all([result.current.optimize(), result.current.optimize()]);
    });
    const posts = fetch.mock.calls.filter(
      ([url, init]) => String(url).endsWith('/optimization-jobs') && init?.method === 'POST',
    );
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0][1]?.body as string)).toEqual({
      operational_day_version_id: version.id,
      idempotency_key: expect.any(String),
    });
    expect(localStorage.getItem(`ramp-job:${version.id}`)).toBe(job.id);
    unmount();
    const restored = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(restored.result.current.job?.id).toBe(job.id));
  });
  it('uses the same submission key after an uncertain network failure', async () => {
    let attempts = 0;
    const fetch = mockService((url, init) => {
      if (url.endsWith('/optimization-jobs') && init?.method === 'POST' && attempts++ === 0)
        return Promise.reject(new TypeError('offline'));
    });
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.version).not.toBeNull());
    await act(() => result.current.optimize());
    expect(result.current.error).toBeInstanceOf(ApiError);
    await act(() => result.current.optimize());
    const bodies = fetch.mock.calls
      .filter(
        ([url, init]) => String(url).endsWith('/optimization-jobs') && init?.method === 'POST',
      )
      .map(([, init]) => JSON.parse(init?.body as string));
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
  });
  it('loads completed results without treating solver completion as readiness', async () => {
    localStorage.setItem(`ramp-job:${version.id}`, job.id);
    mockService((url) =>
      url.endsWith(`/optimization-jobs/${job.id}`)
        ? response({ ...job, status: 'SUCCEEDED', result_run_id: 'run-1' })
        : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() =>
      expect(result.current.result?.result.operational_readiness).toBe(
        'MANUAL_INTERVENTION_REQUIRED',
      ),
    );
    render(
      <JobPanel
        job={result.current.job}
        result={result.current.result}
        error={null}
        busy={false}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Completed')).toBeTruthy();
    expect(screen.getByText('Manual intervention required')).toBeTruthy();
  });
  it('preserves partial results for a cancelled job', async () => {
    localStorage.setItem(`ramp-job:${version.id}`, job.id);
    mockService((url) =>
      url.endsWith(`/optimization-jobs/${job.id}`)
        ? response({ ...job, status: 'CANCELLED', has_partial_result: true })
        : url.endsWith('/result')
          ? response({ ...output, status: 'CANCELLED', partial: true })
          : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.result?.partial).toBe(true));
    expect(result.current.job?.status).toBe('CANCELLED');
  });
  it('never shows results from an earlier input version', async () => {
    localStorage.setItem(`ramp-job:${version.id}`, job.id);
    mockService((url) =>
      url.endsWith(`/optimization-jobs/${job.id}`)
        ? response({ ...job, status: 'SUCCEEDED', result_run_id: 'run-1' })
        : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.result).not.toBeNull());
    act(() => result.current.selectVersion({ ...version, id: 'version-2', version_number: 2 }));
    expect(result.current.result).toBeNull();
    expect(result.current.job).toBeNull();
  });
  it('keeps input and exposes stale-edit errors when revision admission conflicts', async () => {
    mockService((url) =>
      url.endsWith('/revisions')
        ? response({ error: { message: 'Stale parent version', details: [] } }, 409)
        : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.version).not.toBeNull());
    let saved: boolean | undefined;
    await act(async () => {
      saved = await result.current.revise(version.input, 'Shift correction');
    });
    expect(saved).toBe(false);
    expect(result.current.version?.id).toBe(version.id);
    expect(result.current.error?.status).toBe(409);
  });
  it('waits for server cancellation acknowledgement', async () => {
    localStorage.setItem(`ramp-job:${version.id}`, job.id);
    mockService((url) =>
      url.endsWith('/cancel') ? response({ ...job, status: 'CANCELLING' }) : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.job).not.toBeNull());
    await act(() => result.current.cancel());
    expect(result.current.job?.status).not.toBe('CANCELLED');
  });
  it('discards an in-flight response after the supervisor changes versions', async () => {
    localStorage.setItem(`ramp-job:${version.id}`, job.id);
    let finish!: (response: Response) => void;
    mockService((url) =>
      url.endsWith(`/optimization-jobs/${job.id}`)
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(finish).toBeDefined());
    act(() => result.current.selectVersion({ ...version, id: 'other-version' }));
    await act(async () => {
      finish(response({ ...job, status: 'SUCCEEDED', result_run_id: 'run-1' }));
    });
    expect(result.current.job).toBeNull();
    expect(result.current.result).toBeNull();
  });
  it('retains a running state when a status refresh loses the connection', async () => {
    let fail = false;
    mockService((url) =>
      fail && url.endsWith(`/optimization-jobs/${job.id}`)
        ? Promise.reject(new TypeError('offline'))
        : undefined,
    );
    const { result } = renderHook(() => useOperations(version.operational_date));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.optimize());
    await waitFor(() => expect(result.current.job?.status).toBe('RUNNING'));
    fail = true;
    await act(() => result.current.refresh());
    await waitFor(() => expect(result.current.pollError).not.toBeNull());
    expect(result.current.job?.status).toBe('RUNNING');
  });
});

describe('supervisor presentation', () => {
  it('marks changed shifts as manual overrides', () => {
    const day = structuredClone(version.input.operational_day);
    day.employee_shifts[0].end = '2035-04-15T23:00:00-05:00';
    render(
      <Workforce
        day={day}
        baseline={version.input.operational_day}
        result={null}
        editable
        selected={null}
        onSelect={() => {}}
        onEdit={() => {}}
      />,
    );
    expect(screen.getByTitle('Manually revised shift')).toBeTruthy();
  });
  it.each(['QUEUED', 'RUNNING', 'FAILED', 'CANCELLED', 'TIMED_OUT'] as const)(
    'displays %s with truthful diagnostics',
    (status) => {
      render(
        <JobPanel
          job={{ ...job, status, error_code: 'WORKER_LOST' }}
          result={null}
          error={null}
          busy={false}
          onCancel={() => {}}
        />,
      );
      expect(
        screen.getByText(
          status === 'TIMED_OUT' ? 'Timed out' : status[0] + status.slice(1).toLowerCase(),
        ),
      ).toBeTruthy();
    },
  );
  it('filters actual flights and keeps not-evaluated staffing neutral', async () => {
    render(
      <Schedule
        day={version.input.operational_day}
        result={null}
        editable
        onEdit={() => {}}
        onEmployee={() => {}}
      />,
    );
    expect(screen.getByText('Not evaluated')).toBeTruthy();
    await userEvent.type(screen.getByRole('textbox', { name: 'Search flights' }), 'not-a-flight');
    expect(screen.getByText('No flights match these filters.')).toBeTruthy();
  });
  it('sends manual changes as a complete input without mutating the original', async () => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.open = true;
      },
    });
    Object.defineProperty(HTMLDialogElement.prototype, 'close', {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.open = false;
      },
    });
    const save = vi.fn().mockResolvedValue(true);
    render(
      <EditInput
        version={version}
        baseline={version.input}
        target={{ kind: 'flight', index: 0 }}
        busy={false}
        onSave={save}
        onClose={() => {}}
      />,
    );
    await userEvent.click(screen.getByLabelText('Heavy flight'));
    await userEvent.type(
      screen.getByLabelText('Reason for change'),
      'Supervisor heavy-flight review',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Validate & save revision' }));
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0][0].operational_day.flights[0].heavy).toBe(
      !version.input.operational_day.flights[0].heavy,
    );
  });
});
