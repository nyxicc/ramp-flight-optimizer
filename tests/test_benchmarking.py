"""Reproducible benchmark schema, measurement, and file-boundary tests."""

import json
from collections import Counter
from math import isfinite

import pytest

from ramp_optimizer import (
    FlightType,
    OperationalRole,
    Qualification,
    derive_flight_operational_facts,
    intervals_overlap,
    validate_operational_day,
)
from ramp_optimizer.benchmarking import (
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_SCENARIO_NAMES,
    benchmark_results_json,
    build_benchmark_scenarios,
    run_benchmarks,
    write_benchmark_results,
)
from ramp_optimizer.candidates import build_candidate_assignments
from ramp_optimizer.optimizer import optimize_flight_assignments
from tests.invariant_checks import assert_result_invariants


def test_benchmark_scenarios_are_deterministic_valid_and_decision_heavy() -> None:
    scenarios = build_benchmark_scenarios()
    rebuilt = build_benchmark_scenarios()
    candidate_counts: list[int] = []

    assert tuple(item.name for item in scenarios) == BENCHMARK_SCENARIO_NAMES
    assert rebuilt == scenarios
    for scenario in scenarios:
        assert validate_operational_day(scenario.day, scenario.config) == ()
        candidates = build_candidate_assignments(scenario.day, scenario.config)
        candidate_counts.append(len(candidates))
        free_by_flight = Counter(candidate.flight for candidate in candidates)

        assert len(candidates) >= len(scenario.day.flights) * 4
        assert len(candidates) >= len(scenario.day.fixed_assignments) * 10
        assert len(scenario.day.fixed_assignments) <= 2
        assert all(
            free_by_flight[flight] >= scenario.config.minimum_staff
            for flight in scenario.day.flights
        )

    assert candidate_counts[0] < candidate_counts[1] < candidate_counts[2]


def test_full_day_benchmark_contains_the_phase_one_feature_matrix() -> None:
    scenario = build_benchmark_scenarios()[-1]
    facts = tuple(
        derive_flight_operational_facts(flight, scenario.config)
        for flight in scenario.day.flights
    )
    roles = {shift.normalized_role for shift in scenario.day.employee_shifts}
    shift_minutes = {
        int((shift.end - shift.start).total_seconds() // 60)
        for shift in scenario.day.employee_shifts
        if shift.normalized_role is OperationalRole.RAMP_AGENT
    }

    assert {item.flight_type for item in facts} == {
        FlightType.ARRIVAL_ONLY,
        FlightType.DEPARTURE_ONLY,
        FlightType.TURN,
    }
    assert {item.express for item in facts} == {False, True}
    assert {flight.heavy for flight in scenario.day.flights} == {False, True}
    assert {Qualification.PUSH, Qualification.CLOSE_OUT} <= set().union(
        *(employee.qualifications for employee in scenario.day.employees)
    )
    assert {
        OperationalRole.RAMP_AGENT,
        OperationalRole.RAMP_LEAD,
        OperationalRole.TRAINEE,
        OperationalRole.NON_RAMP,
    } <= roles
    assert len(shift_minutes) > 1
    assert any(
        intervals_overlap(
            left.work_start,
            left.work_end,
            right.work_start,
            right.work_end,
        )
        for index, left in enumerate(facts)
        for right in facts[index + 1 :]
    )


@pytest.mark.parametrize("scenario", build_benchmark_scenarios(), ids=lambda item: item.name)
def test_benchmark_results_satisfy_invariants_and_remain_decision_heavy(
    scenario,
) -> None:
    candidates = build_candidate_assignments(scenario.day, scenario.config)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert_result_invariants(scenario.day, scenario.config, result)
    assert result.schedule_summary is not None
    assert result.fairness_metrics is not None
    assert result.continuity_metrics is not None
    assert result.continuity_metrics.eligible_transition_count > 0
    non_fixed_assignments = (
        result.schedule_summary.total_assignments
        - len(scenario.day.fixed_assignments)
    )
    assert len(candidates) * 2 >= non_fixed_assignments * 3
    assert len(scenario.day.fixed_assignments) <= max(
        1,
        result.schedule_summary.total_assignments // 10,
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


def test_default_benchmark_order_and_all_scenario_counts_are_accurate() -> None:
    scenarios = {item.name: item for item in build_benchmark_scenarios()}
    results = run_benchmarks(repeat_count=1)

    assert tuple(item["name"] for item in results["scenarios"]) == (
        BENCHMARK_SCENARIO_NAMES
    )
    for record in results["scenarios"]:
        source = scenarios[record["name"]]
        assert record["employee_count"] == len(source.day.employees)
        assert record["flight_count"] == len(source.day.flights)
        assert record["candidate_assignment_count"] == len(
            build_candidate_assignments(source.day, source.config)
        )
        assert record["fixed_assignment_count"] == len(
            source.day.fixed_assignments
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
