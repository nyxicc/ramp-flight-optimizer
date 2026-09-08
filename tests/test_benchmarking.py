"""Reproducible benchmark schema, measurement, and file-boundary tests."""

import json
from math import isfinite

import pytest

from ramp_optimizer import validate_operational_day
from ramp_optimizer.benchmarking import (
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_SCENARIO_NAMES,
    benchmark_results_json,
    build_benchmark_scenarios,
    run_benchmarks,
    write_benchmark_results,
)
from ramp_optimizer.candidates import build_candidate_assignments


def test_benchmark_scenarios_are_valid_ordered_and_have_real_decisions() -> None:
    scenarios = build_benchmark_scenarios()

    assert tuple(item.name for item in scenarios) == BENCHMARK_SCENARIO_NAMES
    for scenario in scenarios:
        assert validate_operational_day(scenario.day, scenario.config) == ()
        candidates = build_candidate_assignments(scenario.day, scenario.config)
        assert candidates
        assert len(scenario.day.fixed_assignments) < (
            len(scenario.day.employees) * len(scenario.day.flights)
        )


def test_benchmark_schema_counts_repeats_and_timings_are_accurate() -> None:
    scenarios = {item.name: item for item in build_benchmark_scenarios()}
    results = run_benchmarks(
        repeat_count=2,
        scenario_names=("small",),
        generated_at_utc="2035-04-15T00:00:00Z",
    )

    assert results["schema_version"] == BENCHMARK_SCHEMA_VERSION
    assert results["generated_at_utc"] == "2035-04-15T00:00:00Z"
    assert results["settings"]["repeat_count"] == 2
    assert len(results["scenarios"]) == 1
    record = results["scenarios"][0]
    source = scenarios["small"]
    assert record["name"] == "small"
    assert record["employee_count"] == len(source.day.employees)
    assert record["flight_count"] == len(source.day.flights)
    assert record["candidate_assignment_count"] == len(
        build_candidate_assignments(source.day, source.config)
    )
    assert record["fixed_assignment_count"] == len(source.day.fixed_assignments)
    assert record["repeat_count"] == 2
    assert len(record["runs"]) == 2
    for run in record["runs"]:
        assert run["status"] in {"OPTIMAL", "FEASIBLE"}
        assert run["optimization_attempt_count"] >= 1
        assert run["objective_stage_count"] > 0
        assert isfinite(run["solver_runtime_seconds"])
        assert run["solver_runtime_seconds"] >= 0
        assert isfinite(run["wall_clock_seconds"])
        assert run["wall_clock_seconds"] >= 0
    assert isfinite(record["median_wall_clock_seconds"])
    assert record["median_wall_clock_seconds"] >= 0


def test_default_benchmark_order_is_stable() -> None:
    results = run_benchmarks(repeat_count=1)

    assert tuple(item["name"] for item in results["scenarios"]) == (
        BENCHMARK_SCENARIO_NAMES
    )


def test_benchmark_json_round_trip_and_explicit_output(tmp_path) -> None:
    results = run_benchmarks(
        repeat_count=1,
        scenario_names=("small",),
        generated_at_utc="2035-04-15T00:00:00Z",
    )
    serialized = benchmark_results_json(results)
    output = tmp_path / "requested-results.json"

    assert json.loads(serialized) == results
    assert not output.exists()
    assert write_benchmark_results(results, output) == output
    assert json.loads(output.read_text(encoding="utf-8")) == results


def test_running_without_output_path_creates_no_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    run_benchmarks(repeat_count=1, scenario_names=("small",))

    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize("repeat_count", [0, -1, True, 1.5])
def test_invalid_repeat_counts_fail_cleanly(repeat_count) -> None:
    with pytest.raises(ValueError, match="repeat_count"):
        run_benchmarks(repeat_count=repeat_count)


def test_unknown_benchmark_scenario_fails_cleanly() -> None:
    with pytest.raises(ValueError, match="unknown benchmark scenario"):
        run_benchmarks(repeat_count=1, scenario_names=("unknown",))
