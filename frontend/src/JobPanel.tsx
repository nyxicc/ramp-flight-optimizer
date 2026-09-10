import { activeJob, human, type Job, type Result } from './api';
import { Badge } from './ui';

const failures: Record<string, string> = {
  WORKER_LOST:
    'The optimization worker stopped responding. Start the local worker again, then retry optimization.',
  WORKER_INTERRUPTED:
    'The worker was interrupted before optimization finished. Restart the worker and retry.',
  INPUT_INTEGRITY_FAILED:
    'The stored input did not pass its integrity check. Reload a reviewed input version before retrying.',
  SOLVER_EXECUTION_FAILED:
    'The solver process ended unexpectedly. Review the worker diagnostics, then retry optimization.',
  WALL_TIMEOUT:
    'The job exceeded its time limit. Review any partial result; reduce the input size or adjust the solve budget before retrying.',
};
export function JobPanel({
  job,
  result,
  error,
  onCancel,
  busy,
  recovering = false,
}: {
  job: Job | null;
  result: Result | null;
  error: string | null;
  onCancel: () => void;
  busy: boolean;
  recovering?: boolean;
}) {
  if (recovering && !error)
    return (
      <div className="run-status" role="status">
        <Badge tone="info">Restoring job status</Badge>
        <span>Checking the last job for this input version…</span>
      </div>
    );
  if (!job && !error)
    return (
      <div className="run-status">
        <Badge>Not optimized</Badge>
        <span>Review loaded inputs, then optimize the schedule.</span>
      </div>
    );
  return (
    <section className="run-status" aria-live="polite">
      <div className="job-line">
        {job && (
          <>
            <Badge
              tone={
                job.status === 'SUCCEEDED'
                  ? 'good'
                  : ['FAILED', 'TIMED_OUT'].includes(job.status)
                    ? 'bad'
                    : activeJob(job.status)
                      ? 'info'
                      : 'neutral'
              }
            >
              {job.status === 'SUCCEEDED' ? 'Completed' : human(job.status)}
            </Badge>
            <strong>
              {job.status === 'QUEUED'
                ? 'Waiting for an optimization worker'
                : job.status === 'RUNNING'
                  ? human(job.progress.stage_name || job.progress.phase)
                  : job.status === 'CANCELLING'
                    ? 'Cancellation requested; waiting for worker acknowledgement'
                    : job.status === 'CANCELLED'
                      ? 'Optimization cancelled'
                      : job.status === 'SUCCEEDED'
                        ? 'Optimization finished — review operational readiness below'
                        : failures[job.error_code || ''] ||
                          `No completed schedule. Diagnostic: ${job.error_code || 'No diagnostic supplied'}. Review the worker logs before retrying.`}
            </strong>
            {job.status === 'RUNNING' && (
              <span>
                Pass {job.progress.pass_number} · Stage {job.progress.stage_number}
              </span>
            )}
            <span className="spacer" />
            {activeJob(job.status) && (
              <button
                disabled={busy || job.status === 'CANCELLING'}
                onClick={() => {
                  if (
                    confirm(
                      'Cancel this optimization? Any partial result will remain available for review.',
                    )
                  )
                    onCancel();
                }}
              >
                Cancel optimization
              </button>
            )}
            <small className="mono muted" title="Job ID; can be used to restore tracking">
              {job.id}
            </small>
          </>
        )}
        {error && (
          <p className="notice warn" role="alert">
            Status updates interrupted: {error} Retrying automatically. Last known status is shown.
          </p>
        )}
      </div>
      {result && <ResultSummary result={result} />}
    </section>
  );
}
function ResultSummary({ result }: { result: Result }) {
  const r = result.result,
    s = r.schedule_summary;
  const workloads = r.employee_results.flatMap((e) =>
    e.adjusted_workload == null ? [] : [e.adjusted_workload],
  );
  const ready = r.operational_readiness === 'READY';
  const bad = ['MANUAL_INTERVENTION_REQUIRED', 'NO_USABLE_SCHEDULE'].includes(
    r.operational_readiness,
  );
  return (
    <div className="result-summary">
      <div className="readiness">
        <Badge tone={bad ? 'bad' : ready ? 'good' : 'warn'}>{human(r.operational_readiness)}</Badge>
        {result.partial && <Badge tone="warn">Partial result · review required</Badge>}
        <span>
          Solver: {human(r.status)} · {r.solver_runtime_seconds.toFixed(1)}s
        </span>
      </div>
      {s && (
        <dl className="result-metrics">
          {[
            ['Minimum staffed', s.minimum_staffed_flights],
            ['Below preferred', s.total_flights - s.preferred_staffed_flights],
            ['Below minimum', s.below_minimum_flights],
            [
              'Qualification conflicts',
              r.flight_results.filter((f) => f.push_covered === false || f.close_covered === false)
                .length,
            ],
            ['Break violations', s.employees_with_unsatisfied_break],
            ['Lead assignments', s.emergency_lead_assignments],
            ['Three-person teams', r.flight_results.filter((f) => f.staffing_count === 3).length],
            [
              'Workload range',
              workloads.length
                ? `${Math.min(...workloads).toFixed(2)}–${Math.max(...workloads).toFixed(2)}`
                : '—',
            ],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="mono">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
