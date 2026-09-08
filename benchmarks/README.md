# Benchmark methodology and baseline

The benchmark harness measures actual optimizer calls over fixed fictional inputs.
It is intended for reproducible inspection and comparable local runs, not as an
absolute performance promise or CI failure threshold.

## Run it

```bash
python -m ramp_optimizer benchmark --repeat 3
python -m ramp_optimizer benchmark --scenario full-day --repeat 3
python -m ramp_optimizer benchmark --repeat 3 --output benchmark-results.json
```

Without `--output`, JSON is printed and no file is created. With `--output`, only
the explicitly supplied path is written. The checked-in [`baseline.json`](baseline.json)
was produced from the repository implementation with three repetitions per size.

## Scenarios

All three inputs are deterministic subsets of the public, decision-heavy normal
scenario. They use aware datetimes, a fixed solver seed, one solver worker, and a
30-second total time limit per optimizer attempt. The budget is long enough for
the larger input to exercise the lower-priority fairness, workload, and continuity
stages on the baseline machine; it does not guarantee proof completion elsewhere.

| Size | Construction | Features |
|---|---|---|
| `small` | First 3 movements and the fictional morning team | Overlapping decisions, minimum staffing, qualifications, breaks, and the complete objective sequence |
| `medium` | First 12 movements and the fictional daytime team | More shifts and overlaps, qualifications, breaks, fairness, streaks, workload, and continuity |
| `full-day` | 18 movements spanning the early and late teams | Arrivals, departures, turns, Mainline, Express, heavy flights, overlaps, role filtering, protected breaks, all 17 objectives, and readiness reporting |

Small and medium contain one fixed assignment, while full-day contains two,
solely to exercise that feature and break limited assignment symmetry. The
remaining legal candidate sets contain 14, 70, and 103 employee-flight choices,
respectively, so the optimizer chooses the substantial majority of final work.
Every flight has at least three free eligible candidates, including flights that
also contain the fixed record.

## Checked-in baseline results

`baseline.json` was generated at `2026-09-08T17:39:08.551602Z` with CPython
3.12.0 on Windows 11 AMD64, using three repetitions and the documented
single-worker, 30-second solve setting.

| Size | Employees | Flights | Candidates | Fixed | Attempts/run | Final status | Objectives | Readiness | Median solver (s) | Median wall (s) |
|---|---:|---:|---:|---:|---:|---|---|---|---:|---:|
| `small` | 9 | 3 | 14 | 1 | 1 | 3 `OPTIMAL` | 17/17 proven in 3/3 | 3 `MANUAL_INTERVENTION_REQUIRED` | 0.266000 | 0.268019 |
| `medium` | 11 | 12 | 70 | 1 | 1 | 3 `OPTIMAL` | 17/17 proven in 3/3 | 3 `READY` | 6.141000 | 6.144635 |
| `full-day` | 18 | 18 | 103 | 2 | 1 | 2 `OPTIMAL`; 1 `FEASIBLE` | 17 stages reached; 2/3 all proven | 2 `READY`; 1 `READY_WITH_WARNINGS` | 19.047000 | 19.045677 |

Candidate counts are legal, non-fixed employee-flight decisions. The small
subset intentionally demonstrates that solver optimality and operational readiness
are separate; its best legal result still requires intervention. One full-day
repetition used the complete 30-second budget while working on the final continuity
objective. Its usable result and all higher-priority proofs were preserved, the
last stage remained explicitly unproven, and readiness carried the corresponding
solver-proof warning.

## Measurement

For each repetition the harness:

1. validates the scenario through the public validation path;
2. counts legal non-fixed candidates through candidate generation;
3. starts `time.perf_counter()` immediately before the optimizer call;
4. records measured wall-clock time and solver-reported total runtime;
5. records status, objective count/proof state, attempt count, and readiness;
6. reports Python and OS metadata without paths, usernames, or network access.

The headline values are Python `statistics.median` across repetitions. Genuine
timestamps and runtime values vary; schema and scenario ordering are stable.

## JSON schema version 1

The root object contains:

- `schema_version` and `generated_at_utc`;
- `environment`: Python implementation/version, operating system release,
  machine architecture, and CPU description when the platform exposes one;
- `settings`: repeat count and deterministic solver-worker count;
- `scenarios`: ordered `small`, `medium`, and `full-day` result records.

Each scenario records its name and description; employee, flight, candidate, and
fixed-assignment counts; solve limit and repeat count; per-run status, all-objective
proof state, objective count, attempt count, readiness, solver runtime, and wall
runtime; plus median solver and wall runtime.

## Interpretation

Runtime varies by processor, operating system, Python and OR-Tools versions, and
background load. Compare results only when inputs, dependency versions, solve
limits, worker settings, and measurement method match. The baseline documents one
real environment and is not enforced as a speed gate. CI runs a small harness test
and the complete correctness suite, not the full performance benchmark.
