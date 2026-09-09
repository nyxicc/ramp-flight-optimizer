# Architecture

The Ramp Team Flight Optimizer is a layered Python package. Domain functions are
reusable without the command line, and the CP-SAT dependency is contained behind
solver-independent inputs and results.

## Dependency flow

The HTTP boundary is an adapter around the completed engine:

```text
Client
    -> versioned FastAPI adapter
    -> application persistence service
    -> SQLAlchemy repositories / SQLite
    -> Phase 1 application mapping
    -> existing optimizer engine
    -> structured API response
```

The core execution path remains:

```text
OperationalDay + OptimizerConfig
            |
            v
       validation
            |
            v
 timing / classification ---- staffing / workload
            |
            v
       eligibility
            |
            v
  candidate generation
            |
            v
   CP-SAT optimization
            |
            v
 structured OptimizationResult
            |
            v
 operational reporting

sample_data ----> validation / optimizer / reporting <---- CLI
benchmarking ---> validation / candidates / optimizer
ramp_optimizer_api ---> public Phase 1 models / validation / optimizer
ramp_optimizer_api ---> ramp_optimizer_persistence ---> SQLAlchemy
```

Dependencies point toward the domain and solver, not back toward presentation.
`ramp_optimizer` never imports FastAPI or `ramp_optimizer_api`, and production
package modules never import from `tests`.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `models.py` | Frozen, solver-independent input, result, warning, and metric records. Models carry data; they do not solve or print. |
| `enums.py` | Stable domain vocabulary for roles, qualifications, statuses, readiness, and warning codes. |
| `config.py` | Validated operational defaults, role policy, solver determinism, and workbook-import assumptions. |
| `intervals.py` | Half-open interval overlap behavior shared by validation and scheduling. |
| `timing.py` | Flight-number parsing, movement classification, work-window derivation, and Mainline/Express classification. |
| `staffing.py` | Pure minimum, preferred, and maximum staffing derivation. |
| `workload.py` | Exact scaled workload factors and conversion to public values. |
| `validation.py` | Aggregated structural and cross-record validation before decisions are built. |
| `eligibility.py` | Deterministic answers about whether one employee can work one movement and why not. |
| `candidates.py` | Legal non-fixed employee-flight choices, ordered deterministically to reduce the CP-SAT decision space. |
| `optimizer.py` | Constraint construction, sequential lexicographic solves, emergency-pass comparison, and structured result reconstruction. |
| `reporting.py` | Deterministic interpretation of `OptimizationResult`; it neither mutates a result nor makes solver decisions. |
| `teamwork_import.py` | Boundary adapter for a constrained TeamWork-format workbook, with provenance and structured import issues. |
| `sample_data.py` | Public, immutable fictional scenarios made only from normal input models. |
| `benchmarking.py` | Deterministic benchmark sizes, actual optimizer timing, environment metadata, and versioned JSON. |
| `cli.py` | Standard-library argument parsing and orchestration. It contains no scheduling policy or report formatting. |
| `__main__.py` | Process boundary for `python -m ramp_optimizer`; this is where the returned exit code becomes a process exit. |
| `ramp_optimizer_api/schemas.py` | Closed Pydantic version 1 request, response, and error schemas. |
| `ramp_optimizer_api/mapping.py` | Pure conversion between API schemas and public Phase 1 records, including fixed-flight reference resolution. |
| `ramp_optimizer_api/errors.py` | API policy and reference-resolution exceptions outside the optimizer domain. |
| `ramp_optimizer_api/app.py` | FastAPI factory, versioned synchronous routes, and sanitized HTTP exception handling. |
| `ramp_optimizer_api/services.py` | Short transaction orchestration around saved-input optimization. |
| `ramp_optimizer_persistence/` | SQLAlchemy models, repositories, centralized settings, canonical serialization, and integrity checks. |

## Run sequence

1. A caller constructs `OperationalDay` and `OptimizerConfig` values.
2. Validation returns all discovered issues or raises one aggregate
   `InputValidationError` through `validate_or_raise`.
3. Timing, staffing, workload, and eligibility functions derive deterministic
   facts. Candidate generation keeps only legal non-fixed assignment choices.
4. The optimizer adds fixed assignments and constraints, then solves one objective
   at a time. Each proven result is fixed before the next objective.
5. Solver variables are reconstructed into immutable public results, metrics,
   attempt summaries, emergency dispositions, readiness, and warnings.
6. Reporting renders those public records. The CLI only selects a scenario,
   invokes these layers, and writes their output.

## Emergency recovery boundary

The ordinary pass excludes Leads unless the normal role policy explicitly says
otherwise. If emergency recovery is enabled and the ordinary schedule has a
critical minimum-team defect, a second pass may include Lead candidates. The
second pass adds an explicit Lead-minimization objective and is compared with the
ordinary result using public critical outcomes before adoption. Attempt summaries
and disposition fields keep this decision auditable.

## Determinism and side effects

The default solver uses seed `42` and one search worker. Inputs, candidates,
objectives, warnings, and reports have stable ordering. Core optimization has no
network, database, telemetry, or terminal output. File writes occur only at
explicit import/export or CLI boundaries; benchmark output is written only when
the caller supplies a destination.

## Extension boundary

Milestone 18A adds `ramp_optimizer_imports`: frozen review records, closed lifecycle
states, bounded upload/container checks, deterministic revalidation and correction,
and adapter/repository protocols. The FastAPI import routes depend on this service;
the SQLAlchemy import repository implements its transactional persistence boundary.
The TeamWork adapter reuses the existing low-level parser. Confirmation calls domain
validation and the existing operational-day repository in the same transaction as
the unique import linkage. No import operation invokes the optimizer. Preview
retrieval reads canonical stored JSON, and corrections append immutable revisions.

See [IMPORTS.md](IMPORTS.md) for the dependency diagram and contracts. Milestone 18B
can add a format-specific flight review adapter only after structural inspection of
a real sample and construction of a sanitized fictional fixture. This milestone
defines no flight-log schema, columns, or parser.

The API depends on public models and functions and does not duplicate timing,
eligibility, readiness, classification, or reporting policy. Future storage adapters,
job execution, and UI clients can depend on the versioned HTTP contract. The
library and CLI remain executable without importing API infrastructure.
