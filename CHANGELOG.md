# Changelog

This project follows a concise, release-oriented changelog. No formal tagged
release has been created.

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

- Aligned service-class derivation, aggregate validation, invariant checking, and
  documentation on the strict boundary: flight `3000` is Mainline and flight
  `3001` is Express.

### Scope

- Phase 1 remains a standalone Python library and CLI validated with synthetic
  data. It does not include a web API, frontend, database, authentication,
  deployment, OCR, live operational feeds, or production integration.
