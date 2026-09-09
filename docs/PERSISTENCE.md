# Persistence

Milestone 17 adds a local, development-oriented SQLAlchemy 2.x persistence adapter. The dependency flow is `FastAPI -> application service -> repositories -> SQLAlchemy`; the standalone `ramp_optimizer` package does not import the API, persistence, SQLAlchemy, or Alembic layers.

## Local setup

```bash
python -m pip install -e ".[api,dev]"
python -m alembic upgrade head
python -m uvicorn ramp_optimizer_api.app:app --reload --host 127.0.0.1
```

`RAMP_OPTIMIZER_DATABASE_URL` selects the database. Its local default is `sqlite:///./ramp_optimizer.db`. Imports construct an unconnected engine and never create the file or run migrations. Tests inject an engine or session factory. SQLite connections enable foreign-key enforcement. This database is not presented as production-ready, and PostgreSQL has not been verified.

Use `python -m alembic downgrade base` to remove the objects owned by the initial migration, and `python -m alembic upgrade head` to recreate them.

## Schema and identity

`operational_days` owns ordered employee, shift, flight, and fixed-assignment child rows. It stores the resolved optimizer configuration, counts, schema version, UTC creation timestamp, and canonical input hash. `optimization_runs` stores version and status metadata plus the complete immutable `OptimizationResponse` JSON. UUID resource IDs identify database records only. Public flight identity remains its directional arrival/departure number pair; the integer flight-row key exists only for foreign keys.

More than one immutable snapshot may use the same operational date. This milestone exposes no update or delete operation. Employee qualifications use sorted JSON. Shift and flight datetimes use ISO 8601 text, preserving naive values as naive and aware values with their submitted offsets. No implicit UTC conversion occurs.

## Reproducibility and transactions

Canonical JSON contains the complete resolved request, retains meaningful list order, sorts object keys and qualifications, uses compact separators and public enum values, and rejects non-finite floats. SHA-256 is calculated over its UTF-8 bytes. Repositories verify the snapshot hash when loading and before associating a run.

Snapshot creation commits the parent and every child atomically. Running a saved day loads and verifies it in a short read session, closes that session, runs CP-SAT, and opens a separate short write transaction for the complete result. Retrieval reads stored JSON and never invokes the optimizer.

## HTTP resources

The API adds `POST /api/v1/operational-days`, day detail and paginated day listing, `POST /api/v1/operational-days/{id}/optimizations`, run detail, and paginated per-day run listing. Lists default to 20 items, accept nonnegative `offset`, and enforce a maximum `limit` of 100. Day lists optionally filter by `operational_date`.

Malformed UUIDs and pagination use the existing structural `422` envelope. Unknown UUIDs return `RESOURCE_NOT_FOUND` (`404`). Integrity failures return `PERSISTENCE_INTEGRITY_ERROR`; unexpected database failures return sanitized `DATABASE_OPERATION_FAILED`. Solver warnings and manual-intervention readiness remain successful stored results.

Deliberate limitations include SQLite-only verification, synchronous optimization,
immutable operational days without day-edit workflows, and no jobs, workers, UI,
authentication, OCR, or deployment.

## Reviewed imports (Milestone 18A)

Forward migration `20260909_0002` adds `import_jobs` and `import_revisions` without
editing the initial migration. Jobs contain safe metadata, status, current revision,
issue counts, and a unique optional operational-day foreign key. Revisions have a
composite import/revision primary key and store immutable canonical preview and
correction JSON with an integrity hash. Neither table stores workbook bytes, note
text, unmatched names, or client paths. Roster names and reviewed employee data are
retained as necessary for the workflow. Metadata timestamps are UTC.

A conditional revision write serializes edits and confirmations before reading.
Each upload or correction commits its complete preview atomically. Confirmation
creates a day, links it, and records terminal status in one transaction; repeating
the same current revision returns the existing linkage. Foreign keys, confirmation
consistency checks, unique linkage, and revision primary keys enforce integrity.

`alembic downgrade 20260908_0001` removes import tables while preserving all days.
Tests verify upgrade from Milestone 17, downgrade, clean upgrade from base, schema
parity, SQLite foreign keys, uniqueness, and rollback. No migration runs on API
import. [IMPORTS.md](IMPORTS.md) documents retention, hash/duplicate policy, API
contracts, and the distinction between confirmable and optimizable input.
