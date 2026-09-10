# Milestone 20: local background optimization jobs

All examples and fixtures are fictional. Optimization no longer executes inside
HTTP requests. Requests validate and enqueue durable work; a separately launched
local worker claims jobs and supervises one child solver process at a time.
The standalone Python optimizer and CLI remain available for direct execution.

## Start the API and worker

Use the same file-backed SQLite URL and repository working directory in both
terminals. In PowerShell:

```powershell
$env:RAMP_OPTIMIZER_DATABASE_URL = "sqlite:///./ramp_optimizer.db"
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m uvicorn ramp_optimizer_api.app:app --host 127.0.0.1 --port 8000
```

In a second terminal with the same environment:

```powershell
$env:RAMP_OPTIMIZER_DATABASE_URL = "sqlite:///./ramp_optimizer.db"
.venv/Scripts/python.exe -m ramp_optimizer_api.local_worker
```

Use `--once` to process at most one queued job and exit. An idle long-running
worker polls every half-second. Stop it with Ctrl+C. No worker starts implicitly
on application import or HTTP submission. Without a worker, jobs remain durably
queued. In-memory SQLite cannot connect separately launched API/worker processes.
Use one local worker initially; independent workers still cannot claim the same job.

## Public routes

| Method | Route | Response |
| --- | --- | --- |
| POST | `/api/v1/optimization-jobs` | Validate and enqueue version-based work; 202 |
| GET | `/api/v1/optimization-jobs/{job_id}` | Job status, provenance, config and timestamps; 200 |
| GET | `/api/v1/optimization-jobs/{job_id}/progress` | Phase, pass and objective stage; 200 |
| POST | `/api/v1/optimization-jobs/{job_id}/cancel` | Request cancellation or return existing terminal state; 200 |
| GET | `/api/v1/optimization-jobs/{job_id}/result` | Available partial/final result; 200, or 409 if none exists |

The two existing POST routes also now return **202 `JobResponse`**, rather than a
synchronous result or run:

- `/api/v1/optimizations` accepts the existing `OptimizationRequest`, freezes a
  new input snapshot when necessary, and queues it.
- `/api/v1/operational-days/{id}/optimizations` queues the stored input snapshot.
  Confirmed-import readiness checks are retained.

This is an intentional response-contract change. Update clients to poll the job
and result URLs. Existing stored-run GET/list routes remain available; terminal
jobs with results expose their `result_run_id`. Legacy POST retries use a
deterministic key derived from their normalized request. The new version-based
route accepts explicit keys, configuration, and wall timeout.

## Fictional request and response

Use an actual version UUID returned by Milestone 19 in place of the fictional
example UUID below:

```http
POST /api/v1/optimization-jobs
Content-Type: application/json

{
  "operational_day_version_id": "00000000-0000-0000-0000-000000000019",
  "idempotency_key": "fictional-supervisor-solve-001",
  "timeout_seconds": 180
}
```

The 202 resource contains:

```javascript
{
  id: "<job UUID>", status: "QUEUED",
  operational_day_id: "<execution snapshot UUID>",
  operational_day_version_id: "<source version UUID>",
  input_hash: "<64-character canonical input hash>",
  config: {/* complete resolved optimizer configuration */},
  timeout_seconds: 180,
  created_at: "<UTC timestamp>", started_at: null, finished_at: null,
  progress: {phase: "QUEUED", pass_number: 0, stage_number: 0,
             stage_name: null, solver_status: null},
  error_code: null, result_run_id: null, has_partial_result: false
}
```

Omit `config` to use the source version's complete configuration. If supplied,
`config` is a replacement configuration using the existing domain defaults for
omitted fields, not a patch to the source config. The source version remains
unchanged; the job receives a new immutable execution snapshot if its configuration
differs. The source version, execution snapshot, canonical hash and complete config
are all retained, so each run can be reproduced. Legacy snapshots without version
lineage report `operational_day_version_id: null` and still retain their exact
execution snapshot and configuration.

The result endpoint wraps the existing complete `OptimizationResponse`:

```javascript
{
  job_id: "<job UUID>", status: "SUCCEEDED", partial: false,
  result_run_id: "<immutable optimization run UUID>",
  result: {/* existing flight/employee schedules, attempts, objectives,
              warnings, readiness and solver diagnostics */}
}
```

`SUCCEEDED` means the optimizer returned normally, including valid shortage,
unproven/partial, or `NO_USABLE_SCHEDULE` results. It does not mean the schedule is
operationally ready. Inspect the nested result's status and readiness as before.

## State, progress, cancellation and timeouts

State transitions are:

```text
QUEUED -> RUNNING -> SUCCEEDED | FAILED | TIMED_OUT
QUEUED -> CANCELLED
RUNNING -> CANCELLING -> CANCELLED
```

Cancellation and publication serialize in short database transactions. If
cancellation wins, a subsequent result does not change the state to SUCCEEDED.
If publication wins first, cancellation returns the existing terminal resource.
Repeated cancellation is harmless. Running cancellation terminates and joins the
child before releasing the active-job key. The supervisor checks for cancellation
and its wall deadline between pipe reads, normally every 50 ms; database latency
and process cleanup can add delay.

Progress reports actual phases and objective stage names/numbers, with pass 1 for
ordinary work and pass 2 for emergency Lead recovery. No artificial percentage or
ETA is invented. A process-local observer is active only in worker solves. It
emits stage boundaries and completed-stage checkpoints without changing objective
order, eligibility, constraints, or emergency adoption rules.

The job `timeout_seconds` is a hard supervisor budget covering process startup and
solve execution, default 180 seconds, greater than zero and at most 3600. It is
distinct from `config.solver_time_limit_seconds`, the existing **per-pass** domain
budget. Model construction, checkpoint serialization, two-pass recovery and process
startup consume wall time too. A domain-budget expiry can return normally with
partial solver output; a hard wall expiry produces `TIMED_OUT`. The obsolete
60-second synchronous HTTP budget is no longer applied to job admission.

The child also has an independent wall watchdog to bound an orphan after a
supervisor crash. Running jobs are never automatically re-executed. A worker
marks abandoned claims `FAILED / WORKER_LOST` after the original deadline plus
30 seconds of cleanup allowance; CANCELLING claims become CANCELLED. Submit a new
explicit idempotency key after a terminal failure to request a new solve.

## Partial schedules and diagnostics

Completed ordinary objective stages produce durable provisional checkpoints.
Result GET can return them while the job is running. A cancellation, wall timeout
or worker failure preserves the latest successfully persisted checkpoint and,
when present, publishes it as an immutable run in the same transaction as the
terminal job state. If no checkpoint exists, result GET returns
409 `JOB_RESULT_NOT_AVAILABLE`; the status and error code remain available.

Checkpoints are marked `partial: true` and must not be treated as finalized
readiness assessments. They preserve flight and employee schedules, objective
values/proof flags and available attempt information. A currently executing
stage's unpublished incumbent is not guaranteed to survive process termination.
During emergency recovery, the last ordinary checkpoint is retained; provisional
Lead assignments are not exposed before Phase 1's adoption checks finish.
Normal completion replaces the checkpoint with the full finalized result,
including emergency attempts, selected pass, warnings and solver runtime.

Failure codes are safe machine-readable values, including `WALL_TIMEOUT`,
`SOLVER_EXECUTION_FAILED`, `WORKER_PROCESS_EXITED`, `WORKER_INTERRUPTED`,
`WORKER_LOST`, and `INPUT_INTEGRITY_FAILED`. Exception text and filesystem paths
are not returned to HTTP clients.

## Idempotency, persistence and bounds

An explicit key is 1–128 characters and globally scoped to job requests. A retry
with the same effective inputs returns the same job, including after completion.
Different content under that key returns 409 `JOB_IDEMPOTENCY_KEY_REUSED`.
Different keys for the same source, config and timeout share an existing active
job; all key aliases are durably recorded. After it terminates, a new key may
request a new execution. Legacy deterministic keys always resolve to their
original jobs, including cancelled/failed jobs.

Migration **20260910_0005**, following `20260910_0004`, adds:

- `optimization_jobs`: state, input/source FKs, config/hash, timestamps, timeout,
  unique nullable active key, worker claim token, progress/checkpoint JSON,
  error code and unique result-run FK.
- `optimization_job_requests`: unique key, request fingerprint and job FK.

A conditional QUEUED-to-RUNNING update grants exactly one worker the claim.
Every subsequent write checks the claim token and active state. Terminal writes
are fenced; duplicate or stale completion cannot create another run. Result
insertion and terminal publication are one transaction. Losing admission races
roll back any candidate snapshot. Queue lookup is indexed by state/time/ID.
Input snapshots referenced by jobs are protected by the existing ORM history
guards. No job deletion or input-update HTTP route exists.

The admission guard allows up to 1000 outstanding jobs; concurrent admissions for
different inputs can briefly exceed this soft local capacity bound. Request
bodies use the existing bounded reader. Missing resources return 404, invalid
typed/domain input returns 422, oversized bodies return 413, and admission/result
conflicts return the standardized 409 envelope. The queue, snapshots and results
survive process restarts. Upgrade preserves earlier tables and records; downgrade
is allowed with an empty job table and refuses to erase populated job history.

No Redis, Celery, broker, distributed lease service, background web-app thread,
WebSocket, authentication, exports, or dashboard is introduced. Local database
backups, production deployment and multi-host execution remain later work.
