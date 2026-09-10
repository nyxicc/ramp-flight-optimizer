# Milestone 19 verification

Implemented from clean `main` at `dedacc8` (Milestone 18B). All new operational
fixtures are fictional. No commit or push was made. Phase 1 optimizer source was
not changed.

## Formatting, lint, and typing

Run from the repository root in PowerShell using the existing virtual environment:

```powershell
$milestoneFiles = @(git diff --name-only -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
.venv/Scripts/python.exe -m ruff format --check $milestoneFiles
.venv/Scripts/python.exe -m ruff check $milestoneFiles
.venv/Scripts/python.exe -m mypy --follow-imports=silent --ignore-missing-imports src/ramp_optimizer_api/input_schemas.py src/ramp_optimizer_api/input_services.py src/ramp_optimizer_api/input_routes.py src/ramp_optimizer_persistence/versions.py src/ramp_optimizer_persistence/models.py src/ramp_optimizer_api/app.py src/ramp_optimizer_api/import_routes.py
```

Final results: **11 files already formatted**, **all lint checks passed**, and
**no typing issues in 7 source files**. This is the affected scope, consistent
with the earlier milestone's typing baseline.

## Focused workflows and migrations

```powershell
.venv/Scripts/python.exe -m pytest tests/test_input_management.py tests/test_input_migrations.py tests/test_import_migrations.py tests/test_persistence_migrations.py tests/test_api_app.py -q
```

Final result: **31 passed**, 12 dependency deprecation warnings, **10.38 seconds**.
Coverage includes complete snapshots, source imports/versions, lineage, employee
enable/disable and qualifications, shift changes, flight changes, heavy flags,
fixed assignment changes, staffing/coverage/overlap checks, bounds, missing IDs,
key reuse, stale hashes, concurrent retries and conflicting edits, rollback after
late persistence failure, ORM history protection, and optimization equivalence.
Qualification removal is verified against the existing optimizer's crew warnings.

Migration checks upgrade populated Milestone 17/18 inputs, compare metadata,
downgrade/re-upgrade an empty version table, preserve employee and flight import
history, and reject downgrade when immutable version history would be lost.

```powershell
.venv/Scripts/python.exe -m alembic heads
git diff --check
```

Results: **20260910_0004 (head)**; no whitespace errors.

## Full regression

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Final result: **907 passed, 0 failed, 0 skipped**, **17 dependency deprecation
warnings**, **228.02 seconds**. Warnings concern the existing Starlette/HTTPX
integration and Alembic configuration. This includes the full earlier-milestone
regression suite and 26 new fictional input-management/migration tests.

An initial regression found the expected outdated OpenAPI inventory assertion;
it was updated to include all four new routes. A later overlapping verification
attempt collided on pytest temporary-directory initialization and was stopped.
Final verification runs execute sequentially, without weakening assertions.

## Deferred scope

Background jobs/workers/queues, progress and cancellation, React/Next.js UI,
schedule-result edits/locks, authentication/authorization, audit-user identity,
exports, PostgreSQL deployment and distributed infrastructure remain deferred
to Milestones 20–28. Revisions use the existing synchronous optimization routes.
