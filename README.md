# Ramp Team Flight Optimizer

A deterministic Python decision-support engine that assigns a ramp team to a day
of aircraft movements while preserving operational constraints and making staffing
tradeoffs explicit. It uses OR-Tools CP-SAT and a sequential, 17-stage
lexicographic objective hierarchy rather than one blended score.

> All employees, flight numbers, times, gates, qualifications, and scenarios in
> this repository are newly constructed fictional data. This project demonstrates
> a synthetic decision-support optimizer; it does not claim live production use.

## Project status

Phase 1—the standalone optimization engine—is complete. It includes validated
domain models, timing and eligibility rules, assignment optimization, structured
results and warnings, human-readable reporting, fictional demos, reproducible
benchmarks, packaging, and automated tests.

The repository intentionally does not include a web UI, API server, database,
authentication, deployment, OCR, schedule-image parsing, or live airline data.
Those integration concerns belong to a possible Phase 2.

## Capabilities

- validates employees, shifts, flights, qualifications, breaks, and fixed assignments;
- derives arrival, departure, and turn work windows with aware datetimes;
- filters deterministic legal employee-flight candidates before solving;
- prioritizes minimum staffing, qualification coverage, breaks, preferred staffing,
  fairness, streak control, adjusted workload, and continuity in a fixed order;
- can attempt an explicit emergency Lead recovery pass when enabled;
- returns usable partial schedules when full operational goals cannot be met;
- separates mathematical solver status from operational readiness;
- emits stable structured warnings and a deterministic supervisor-facing report;
- imports a validated TeamWork-format workbook without making it a live-data source;
- supplies public fictional scenarios and a real-optimizer benchmark harness.

## Install

Python 3.12 or newer is required. Create and activate an isolated environment
before installing the package.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The editable installation provides both the `ramp-optimizer` console command and
the equivalent `python -m ramp_optimizer` module command. Runtime dependencies are
bounded in `pyproject.toml`; CLI, JSON, timing, platform, and statistics support
use the Python standard library.

## Quick start

```bash
ramp-optimizer --help
ramp-optimizer demo --scenario normal
ramp-optimizer demo --scenario shortage
ramp-optimizer demo --scenario emergency-lead
```

Module execution is equivalent:

```bash
python -m ramp_optimizer demo --scenario normal
```

An optional positive, finite total solve budget may be supplied:

```bash
ramp-optimizer demo --scenario normal --time-limit 10
```

The CLI is a thin adapter: it builds a fictional input, calls the existing
validation and optimizer functions, and sends the structured result to the
existing reporting layer.

### Demo scenarios

| Scenario | Purpose | Expected readiness |
|---|---|---|
| `normal` | 24 decision-heavy mixed movements, overlapping windows, multiple shifts, qualifications, protected breaks, workload, and continuity | Operationally ready without emergency recovery; may be `READY_WITH_WARNINGS` when the solve budget ends before every lower-priority objective is proven |
| `shortage` | A valid flight with too few eligible Agents | `MANUAL_INTERVENTION_REQUIRED` with a useful partial schedule |
| `emergency-lead` | Agent-only minimum staffing is impossible, but one Lead can recover it | `READY_WITH_WARNINGS` with explicit Lead use |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The command succeeded and produced a valid usable result, including a warned partial schedule. |
| `1` | No usable optimization result was produced, an output could not be written, or an unexpected supported runtime failure occurred. |
| `2` | Command usage or input arguments were invalid. |

A staffing shortage is an operational outcome, not a software crash, so the
documented `shortage` demo exits with code `0` and preserves its warnings.

### Example report

The beginning of the normal fictional report is:

```text
Ramp Team Flight Optimizer
Readiness: READY_WITH_WARNINGS
Solver: FEASIBLE; all objectives proven optimal: no
Emergency recovery: enabled=no; disposition=NOT_ENABLED; Lead assignments=0
Flights: 24; minimum staffed 24/24; below minimum 0; preferred staffed 3
Qualifications: compliant 15/15; missing push 0; missing close-out 0
Breaks: satisfied 16; unsatisfied 0; not evaluable 0
Assignments: 75; participating employees 16
Lead interventions:
- None
Warnings:
- [WARNING] SOLVER_RESULT_NOT_PROVEN_OPTIMAL: A usable schedule was returned, but not every optimization stage was proven optimal.
```

This measured example exhausted the normal scenario's 30-second default while
preserving complete minimum staffing, qualification coverage, and protected
breaks. A faster environment may prove all 17 stages and report `READY`. Exact
runtimes and assignments depend on the configured budget and installed solver
version; report sections and record ordering are stable.

## Flight model in brief

- An **arrival** has an arrival flight number and ETA but no departure side.
- A **departure** has a departure flight number and ETD but no arrival side.
- A **turn** contains both sides, with departure later than arrival.
- ETA and ETD are scheduled times. With defaults, arrival-only work runs from
  ETA − 10 minutes through ETA + 20 minutes, departure-only work runs from ETD −
  60 minutes through ETD, and a turn runs from ETA − 10 minutes through ETD.
- **Mainline** has a parsed numeric flight number at or below `3000`.
- **Express** has a parsed numeric flight number greater than `3000`.
- Both legs of a turn must be in the same service class; mixed turns are invalid.
- A **heavy** movement raises preferred and maximum staffing from four to five;
  the default minimum remains three.

`Flight` is the immutable movement identity used by assignments: its arrival and
departure numbers and times, gate, and heavy flag form the domain value. Parsed
numeric values establish directional uniqueness, so carrier prefixes or leading
zeroes do not create a second identity for the same directional flight number.

See [Domain rules](docs/DOMAIN_RULES.md) for the complete validated behavior.

## Architecture

```text
Input models
    -> validation
    -> timing / classification
    -> eligibility
    -> candidate generation
    -> CP-SAT optimizer
    -> structured result
    -> operational reporting

sample data / CLI / benchmark -> orchestrate the same public layers
```

Models carry data, timing derives facts, eligibility answers business-rule
questions, candidate generation reduces the solver decision space, the optimizer
handles assignment tradeoffs, and reporting interprets without changing the
result. The CLI owns no scheduling policy. See [Architecture](docs/ARCHITECTURE.md).

## Optimization and readiness

The ordinary solve optimizes 17 objectives sequentially. After each proven
optimum, that value is fixed before the next stage is attempted, so a later
fairness or continuity goal cannot damage an earlier staffing, qualification, or
break result. See [Optimization objectives](docs/OPTIMIZATION_OBJECTIVES.md).

Solver status answers whether CP-SAT found or proved a mathematical solution.
Operational readiness answers whether the returned schedule is safe to use as-is.
An `OPTIMAL` result can still be `MANUAL_INTERVENTION_REQUIRED` when the best
possible solution is below minimum staffing or qualification requirements.
Conversely, a timed result may remain usable while reporting that not all
objectives were proven optimal.

Partial schedules are preserved with warnings. Emergency Lead use is an explicit,
audited recovery mechanism: Leads do not silently join normal Agent staffing, and
an emergency attempt is adopted only under the implemented comparison policy.
See [Operational readiness](docs/OPERATIONAL_READINESS.md).

## Tests

Install development dependencies and run:

```bash
python -m pytest
```

The suite covers validation, timezone and interval boundaries, eligibility,
fixed assignments, staffing and qualifications, breaks, fairness, streaks,
adjusted workload, continuity, emergency recovery, timeouts, reporting, the full
synthetic day, public scenarios, CLI behavior, benchmark schema, and package
boundaries. Tests do not use absolute elapsed-time pass/fail thresholds.

## Benchmarks

Run all deterministic sizes three times:

```bash
python -m ramp_optimizer benchmark --repeat 3
```

Run one size or explicitly save versioned JSON:

```bash
ramp-optimizer benchmark --scenario full-day --repeat 3
ramp-optimizer benchmark --repeat 3 --output benchmark-results.json
```

Without `--output`, the command prints JSON and creates no file. The checked-in
[benchmark methodology and baseline](benchmarks/README.md) records real optimizer
runs and is documentation—not a machine-independent performance gate.

## Limitations and non-goals

- Inputs are trusted structured models or a constrained workbook import, not live
  operational feeds.
- Synthetic workload multipliers are explainable defaults, not empirically
  calibrated labor standards.
- The engine proposes assignments; a qualified supervisor remains responsible
  for operational review and intervention.
- Runtime varies with hardware, dependency versions, scenario complexity, and
  solve budget.
- Phase 1 is a library and CLI. It has no FastAPI, Flask, React, database,
  authentication, deployment, Docker requirement, OCR, telemetry, or network calls.

## Phase 2 direction

A future Phase 2 may place a carefully designed API and non-technical UI around
the stable structured models, add persistence and authenticated workflows, and
connect approved data sources. Those additions should preserve the engine’s pure
layering, explicit warnings, deterministic sample mode, and solver/readiness
distinction rather than moving business rules into integration code.
