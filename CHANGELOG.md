# Changelog

This project follows a concise, release-oriented changelog. No formal tagged
release has been created.

## Unreleased - Phase 2 Milestone 21 — Supervisor dashboard foundation

- Added batch review corrections, a single schedule-day interpretation that ignores
  workbook title dates, and named Ramp Agent imports without a required JSON roster.
  New workbook identities start without qualifications; verified roster values are preserved.

- Added a React/TypeScript supervisor workspace for operating-date selection,
  reviewed imports, flight and workforce tables, operational issues and settings.
- Connected immutable input revisions and asynchronous optimization admission,
  polling, cancellation, partial results and per-version job recovery.
- Added deliberate manual edits, original-value restoration, fixed-assignment
  markers, result readiness summaries and a server-backed assignment timeline.
- Added frontend contract types, component/lifecycle tests, CI and local setup
  documentation. Existing optimizer and backend behavior are unchanged.

## Unreleased — Phase 2 Milestone 18B

- Add a reviewed daily flight-log adapter using the existing Flight model and domain rules.
- Extend upload, correction, historical revision, and confirmation APIs for flights.
- Combine confirmed employee and flight imports into immutable operational-day inputs.
- Add migration `20260909_0003`, worksheet/global-cell limits, and fictional import tests.
- Document explicit time policy, conservative status handling, privacy, and format limitations
  in [Flight imports](docs/FLIGHT_IMPORTS.md).

## Unreleased — Phase 2 Milestone 17

### Added

- SQLAlchemy 2.x operational-day snapshots and complete immutable optimization-run results.
- Alembic revision `20260908_0001`, centralized SQLite configuration, canonical SHA-256 input snapshots, six persistent resource routes, and reproducibility coverage.

### Scope

- Persistence remains synchronous and development-oriented. No jobs, frontend, authentication, import revision workflow, OCR, editing, or deployment was added.

## Unreleased — Phase 2 Milestone 16

### Added

- A `/api/v1` FastAPI application factory and Uvicorn application exposing health,
  package version, operational-day validation, and synchronous optimization.
- Explicit Pydantic request, result, warning, metric, attempt, objective, Lead
  intervention, validation, and error-envelope schemas.
- Pure API-to-domain mapping with canonical directional fixed-flight references,
  deterministic JSON serialization, and complete `OptimizationResult` coverage.
- API application, mapping, validation, optimization, equivalence, OpenAPI, and
  sanitized-error regression tests plus detailed API documentation.

### Changed

- Added bounded optional FastAPI/Uvicorn dependencies and a compatible HTTPX test
  dependency; CI now installs API and development extras and imports the app.
- Extended the architecture documentation with the versioned API adapter while
  preserving the standalone Phase 1 library and CLI boundary.

### Scope

- Optimization remains synchronous and in-memory. No persistence, background jobs,
  frontend, authentication, deployment, OCR, or live-data integration was added.

## Unreleased — Phase 1 portfolio release

### Added

- Solver-independent employee, shift, flight, qualification, fixed-assignment,
  warning, metric, attempt, and optimization result models.
- Deterministic timing, Mainline/Express classification, eligibility, candidate
  generation, staffing, workload, continuity, and TeamWork-format import layers.
- OR-Tools CP-SAT assignment engine with 17 sequential lexicographic objectives,
  protected breaks, qualification-aware staffing, fairness, streak controls,
  shift-adjusted workload, and team continuity.
- Explicit two-pass emergency Lead recovery with intervention reasons, disposition,
  attempt audit records, and structured warnings.
- Deterministic human-readable operational reporting that separates solver status
  from operational readiness and preserves partial schedules.
- Public fictional normal, staffing-shortage, and emergency-Lead scenarios.
- `ramp-optimizer` and `python -m ramp_optimizer` demo and benchmark commands.
- Versioned real-optimizer benchmark JSON, methodology, release documentation,
  packaging smoke coverage, and GitHub Actions verification.
- Comprehensive unit, integration, invariant, boundary, adversarial, timeout,
  reporting, synthetic-day, CLI, scenario, and benchmark tests.

### Fixed

- Replaced the nearly predetermined demo and benchmark construction with
  decision-heavy scenarios: the normal demo now has two fixed assignments and 141
  free candidates, while the benchmark sizes have increasing `14`, `70`, and
  `103`-candidate search spaces with one, one, and two fixed assignments.
- Corrected virtual-environment setup instructions so installation runs only after
  the environment is activated on Windows PowerShell or macOS/Linux.
- Aligned service-class derivation, aggregate validation, invariant checking, and
  documentation on the strict boundary: flight `3000` is Mainline and flight
  `3001` is Express.

### Scope

- Phase 1 remains a standalone Python library and CLI validated with synthetic
  data. It does not include a web API, frontend, database, authentication,
  deployment, OCR, live operational feeds, or production integration.

## Milestone 19 — Input-management API

Validated immutable operational-day drafts and complete-snapshot revisions are
available under `/api/v1`, with optimistic concurrency, idempotency, and preserved
lineage. Apply migration `20260910_0004`. See the [workflow, routes, validation and
persistence contract](docs/INPUT_MANAGEMENT.md) and [verification record](docs/INPUT_MANAGEMENT_VALIDATION.md).

## Milestone 20 — Background optimization jobs

Optimization POST routes now return HTTP 202 jobs. A separately launched local
worker claims durable work and supervises a killable solver process, retaining
partial checkpoints, diagnostics, exact input provenance and configuration.
Migration `20260910_0005` adds the queue and idempotency records without changing
earlier history. See [background jobs](docs/BACKGROUND_JOBS.md)
and the [verification record](docs/BACKGROUND_JOBS_VALIDATION.md).
