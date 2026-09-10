# Milestone 20 verification

Work continued on `main` at `dedacc8`, preserving the uncommitted Milestone 19
implementation. No commit or push was made. All operational fixtures are fictional.

## Formatting, lint and static typing

```powershell
$milestoneFiles = @(git diff --name-only -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
.venv/Scripts/python.exe -m ruff format --check $milestoneFiles
.venv/Scripts/python.exe -m ruff check $milestoneFiles
.venv/Scripts/python.exe -m mypy --follow-imports=silent --ignore-missing-imports src/ramp_optimizer_api/job_schemas.py src/ramp_optimizer_api/job_services.py src/ramp_optimizer_api/job_routes.py src/ramp_optimizer_api/local_worker.py src/ramp_optimizer_persistence/jobs.py src/ramp_optimizer_persistence/models.py src/ramp_optimizer_api/app.py src/ramp_optimizer/execution.py src/ramp_optimizer/optimizer.py
```

Results: **24 files already formatted**, **all lint checks passed**, and **no
typing issues in 9 source files**. The formatter also normalized the existing
optimizer file. Three existing typing issues in that affected module were fixed
with distinct local variable names and a compatible linear-expression annotation;
no scheduling constraints or objectives were altered.

## Focused verification

```powershell
.venv/Scripts/python.exe -m pytest tests/test_jobs.py tests/test_job_migrations.py tests/test_api_app.py tests/test_api_endpoints.py tests/test_persistence_api.py tests/test_input_management.py -q
```

Result: **66 passed**, 7 dependency deprecation warnings, **52.76 seconds**.
The existing API equivalence tests now explicitly submit a job, run the actual
worker outside HTTP, retrieve its result, and retain their original assertions
about assignments, attempts, emergency recovery, diagnostics and readiness.

```powershell
.venv/Scripts/python.exe -m pytest tests/test_job_migrations.py tests/test_input_migrations.py tests/test_import_migrations.py tests/test_persistence_migrations.py tests/test_api_app.py -q
```

Result: **7 passed**, 17 dependency deprecation warnings, **7.31 seconds**.
Includes prior data preservation, schema comparison, foreign-key checks,
upgrade/downgrade/re-upgrade, and refusal to drop populated job history.

After allowing the crash-specific test adequate Windows process startup time:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_jobs.py tests/test_job_migrations.py -q
```

Result: **14 passed**, 7 dependency deprecation warnings, **24.78 seconds**.
Coverage includes concurrent admission/claims, idempotency aliases, stale-worker
fencing, hard timeout, child failure, queued/running cancellation, retained partial
results, atomic run publication/rollback, abandoned claims, HTTP admission without
solver execution, request bounds and exact version/configuration provenance.

## Complete regression

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Final sequential regression: **921 passed, 0 failed, 0 skipped**, **22 dependency
deprecation warnings**, **282.49 seconds**. This includes all earlier milestone
tests and the 14 new job/migration cases. Warnings concern the existing
Starlette/HTTPX integration and Alembic configuration.

The first full run passed 920 tests and exposed a timing collision in the crash
fixture: Windows spawn/import startup exhausted its two-second timeout before
the deliberate child exit. The crash fixture now has a separate 30-second budget;
the hanging-child timeout fixture retains its two-second bound. Failure/status
assertions were retained. The partial-result timeout fixture also allows startup
time before testing checkpoint retention.

## Entry point and migration

```powershell
.venv/Scripts/python.exe -m ramp_optimizer_api.local_worker --help
.venv/Scripts/python.exe -m alembic heads
git diff --check
```

Results: worker help lists `--once`; Alembic reports **20260910_0005 (head)**;
no whitespace errors. Tests launch real spawned solver processes and verify cleanup.
No development server or persistent worker is left running by implementation.
