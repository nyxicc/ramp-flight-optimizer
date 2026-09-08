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

All three inputs are deterministic subsets or the complete form of the public
normal scenario. They use aware datetimes, a fixed solver seed, one solver worker,
and a five-second total time limit per optimizer attempt.

| Size | Construction | Features |
|---|---|---|
| `small` | First 3 movements and the fictional morning team | Real candidates, fixed assignments, overlapping decisions, staffing, and qualifications |
| `medium` | First 12 movements and the fictional daytime team | More shifts and overlaps, qualifications, breaks, fairness, streaks, workload, and continuity |
| `full-day` | All 24 movements and all 21 fictional employees | Arrivals, departures, turns, Mainline, Express, heavy flights, overlaps, fixed work, role filtering, protected breaks, all 17 objectives, and readiness reporting |

The scenarios deliberately contain non-fixed candidate assignments. Fixed records
exercise the real fixed-assignment path but do not predetermine the result.

## Checked-in baseline results

`baseline.json` was generated at `2026-09-08T16:24:52.421416Z` with CPython
3.12.0 on Windows 11 AMD64, using three repetitions and the documented
single-worker, five-second solve setting.

| Size | Employees | Flights | Candidates | Fixed | Attempts/run | Final status | Objectives | Readiness | Median solver (s) | Median wall (s) |
|---|---:|---:|---:|---:|---:|---|---|---|---:|---:|
| `small` | 9 | 3 | 3 | 9 | 1 | `OPTIMAL` | 17/17 proven | `MANUAL_INTERVENTION_REQUIRED` | 0.047000 | 0.042565 |
| `medium` | 11 | 12 | 4 | 36 | 1 | `OPTIMAL` | 17/17 proven | `READY` | 0.110000 | 0.113335 |
| `full-day` | 21 | 24 | 6 | 72 | 1 | `OPTIMAL` | 17/17 proven | `READY` | 0.265000 | 0.258828 |

Candidate counts are legal, non-fixed employee-flight decisions. The small
subset intentionally demonstrates that solver optimality and operational readiness
are separate; its best legal result still requires intervention.

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
