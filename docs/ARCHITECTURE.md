# Architecture

The Ramp Team Flight Optimizer is a layered Python package. Domain functions are
reusable without the command line, and the CP-SAT dependency is contained behind
solver-independent inputs and results.

## Dependency flow

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
```

Dependencies point toward the domain and solver, not back toward presentation.
Production package modules never import from `tests`.

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

A future API or UI should depend on the public models and functions. It should not
duplicate timing, eligibility, readiness, or reporting policy. That keeps one
authoritative scheduling engine and lets the library and CLI remain executable
without infrastructure.
