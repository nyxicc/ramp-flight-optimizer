# Milestone 18A validation record

Verified on Windows using the repository's Python 3.12 virtual environment.
The latest `main` was pulled before implementation (Milestone 17 base `c576231`).
The original migration and optimizer business-rule implementations are unchanged.

## Environment

| Dependency | Version |
|---|---|
| Python | 3.12.0 |
| pytest | 8.4.2 |
| OR-Tools | 9.15.6755 |
| openpyxl | 3.1.5 |
| FastAPI | 0.141.1 |
| Pydantic | 2.13.5 |
| SQLAlchemy | 2.0.52 |
| Alembic | 1.19.2 |
| python-multipart | 0.0.32 |
| Starlette | 1.6.0 |

`python-multipart>=0.0.20,<0.1` is the only added project dependency. It belongs to
the API extra and enables multipart uploads. Existing API/dev extras were installed
into the existing virtual environment to run the full baseline and final suites.

## Test results

All commands below used that environment's `python -m pytest`.

| Run | Passed | Failed | Skipped | Runtime |
|---|---:|---:|---:|---:|
| Complete pre-change baseline | 742 | 0 | 0 | 204.37 s |
| Final focused import/parser tests | 111 | 0 | 0 | 15.54 s |
| Final API compatibility and import tests | 128 | 0 | 0 | 18.18 s |
| Final persistence and migration tests | 12 | 0 | 0 | 5.83 s |
| Complete final suite | **836** | **0** | **0** | **218.49 s** |

The milestone adds 94 collected tests, including parametrized cases. The existing
OpenAPI route-set assertion was extended for the five new paths; existing endpoint
and optimizer behavior tests still pass.

```bash
python -m pytest tests/test_import_workflow.py tests/test_teamwork_import.py -q
python -m pytest tests/test_api_app.py tests/test_api_endpoints.py tests/test_api_mapping.py tests/test_persistence_api.py tests/test_import_workflow.py -q
python -m pytest tests/test_persistence.py tests/test_persistence_api.py tests/test_persistence_migrations.py tests/test_import_migrations.py -q
python -m pytest -q
```

The full run emitted seven dependency/configuration deprecation warnings: two
Starlette/httpx/AnyIO warnings and five instances of Alembic's existing missing
`path_separator` setting warning. There were no failed or skipped tests. No strict
timing assertions were added.

## Migration and integrity checks

- Upgraded from the published Milestone 17 schema while preserving an existing day.
- Confirmed an import on the new schema and verified unique day linkage.
- Downgraded to Milestone 17 while preserving both existing and confirmed days.
- Downgraded to base and upgraded cleanly through both migrations.
- Compared the resulting schema with SQLAlchemy metadata: no differences.
- Verified SQLite foreign keys, revision primary-key uniqueness, closed status/type
  constraints, confirmation/linkage consistency, and deletion restrictions.
- Checked concurrent corrections (one revision winner) and confirmations (one day).
- Injected failures after staged upload, correction, and confirmation writes:
  transactions rolled back without partial revisions or orphan snapshots.

A separately created temporary clean database was inspected directly. It contained
eight application tables plus `alembic_version`. `import_jobs` has 19 columns,
an import UUID primary key, a unique optional day foreign key, and four named check
constraints. `import_revisions` has five columns, composite `(import_id, revision)`
primary key, parent foreign key, and positive-revision constraint.
`PRAGMA foreign_key_check` returned no violations. This inspection database was
removed after disposal; no developer database was used.

## Privacy, boundaries, and scope audit

The staged source/documentation/test inventory was searched for common credential
and private-key patterns, developer-specific absolute paths, and workbook/PDF/CSV
binaries. No matches were found. The distinctive fictional note sentinel appears
only in its fixture generator, where it is used to prove that note content does not
reach parsed results, reviewed responses, database values, errors, or captured logs.
All newly introduced employee names and IDs are explicitly fictional. Path-like
filenames in tests are inert fictional attack cases, not stored server paths.

Upload reads stop at the size bound, uploads close on failures as well as success,
and archive checks cover decompression/member/worksheet limits, malformed XML,
DTD/entity declarations, macros, and disguised internal XML parts. Canonical JSON
round trips, issue persistence, preview hash tampering, independent duplicate
uploads, revision conflicts, and latest-revision idempotency are covered.

No flight-log parser or guessed schema was added. Import upload, preview,
correction, and confirmation do not call optimization. Confirmation uses existing
domain validation and creates an employee-only immutable snapshot; optimizing that
stored imported snapshot is blocked with `FLIGHT_DATA_REQUIRED`. Dependency tests
keep HTTP/database imports outside the optimizer and generic import layers.

`git diff --cached --check` passed. Scope review found changes limited to the
existing parser's safe review output, import application/API/persistence layers,
one forward migration, fictional tests, the multipart dependency, and documentation.

## Files and assumptions

The implementation adds the `ramp_optimizer_imports` package, API import schemas
and routes, SQL import repository, migration `20260909_0002`, fictional fixture and
workflow/migration tests, and the import guide. It extends existing domain import
results, parser input handling, application wiring, persistence models, day response
eligibility, and stored imported-day optimization gating. README, API, architecture,
and persistence documentation describe the completed behavior.

The authoritative roster and operational date are supplied explicitly with the
upload. Existing TeamWork defaults (including the 18-hour maximum shift) apply.
Dates/times remain naive for workbook imports; reviewed datetime awareness is
validated by the domain. Unknown roles remain non-ramp-eligible warnings and never
grant qualifications. Raw workbook bytes are not retained permanently; bounded
multipart spools are request-owned temporary resources.

Only SQLite has been verified. This remains a local development API without
authentication or production upload/storage controls. A confirmed snapshot cannot
be edited; adding structured flights requires a new operational-day resource.
Milestone 18B remains blocked on learning the actual flight-log structure and
creating an appropriate sanitized fictional fixture; no format is presumed.

See [IMPORTS.md](IMPORTS.md) for the complete lifecycle, five endpoints, correction
semantics, configuration limits, retention policy, error vocabulary, and adapter
extension seam.
