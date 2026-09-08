"""Cross-layer consistency and predictable input-transformation tests."""

from dataclasses import replace
from datetime import timedelta

import pytest

from ramp_optimizer import (
    BreakStatus,
    Employee,
    FixedAssignment,
    OperationalRole,
    OptimizerConfig,
    Qualification,
    assess_employee_flight_eligibility,
    derive_work_window,
    format_optimization_report,
    optimize_flight_assignments,
)
from tests.invariant_checks import (
    assert_result_invariants,
    lexicographic_values,
    semantic_projection,
)
from tests.scenario_builders import (
    SyntheticScenario,
    arrival,
    at,
    operational_day,
    optimizer_config,
    small_ready_scenario,
    synthetic_employee,
    synthetic_shift,
)


@pytest.mark.integration
@pytest.mark.slow
def test_all_major_full_day_results_obey_the_same_aggregate_contract(
    canonical_scenario: SyntheticScenario,
    canonical_result,
    recoverable_emergency_scenario: SyntheticScenario,
    recoverable_emergency_result,
    insufficient_emergency_scenario: SyntheticScenario,
    insufficient_emergency_result,
) -> None:
    for scenario, result in (
        (canonical_scenario, canonical_result),
        (recoverable_emergency_scenario, recoverable_emergency_result),
        (insufficient_emergency_scenario, insufficient_emergency_result),
    ):
        assert_result_invariants(scenario.day, scenario.config, result)
        assert result.schedule_summary is not None
        assert result.schedule_summary.total_assignments == sum(
            flight.staffing_count for flight in result.flight_results
        ) == sum(employee.flight_count for employee in result.employee_results)
        assert result.schedule_summary.emergency_lead_assignments == len(
            result.lead_assignments
        )


def test_reordering_employees_preserves_semantic_outcome() -> None:
    scenario = small_ready_scenario()
    original = optimize_flight_assignments(scenario.day, scenario.config)
    reordered_day = replace(
        scenario.day,
        employees=tuple(reversed(scenario.day.employees)),
        employee_shifts=tuple(reversed(scenario.day.employee_shifts)),
    )
    reordered = optimize_flight_assignments(reordered_day, scenario.config)

    left = semantic_projection(original)
    right = semantic_projection(reordered)
    assert left["flights"] == right["flights"]
    assert left["employees"] == right["employees"]
    assert left["fairness"] == right["fairness"]
    assert left["objectives"] == right["objectives"]


def test_reordering_flights_preserves_coverage_and_fairness() -> None:
    workers = tuple(
        synthetic_employee(
            f"R{index:03}",
            qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT),
        )
        for index in range(1, 4)
    )
    flights = (arrival("FX101", at(9)), arrival("FX102", at(11)))
    day = operational_day(
        employees=workers,
        shifts=tuple(
            synthetic_shift(worker.employee_id, start=at(7), end=at(13))
            for worker in workers
        ),
        flights=flights,
    )
    config = optimizer_config(
        normal_preferred_staff=3,
        heavy_preferred_staff=3,
        solver_time_limit_seconds=2.0,
    )

    original = optimize_flight_assignments(day, config)
    reordered = optimize_flight_assignments(
        replace(day, flights=tuple(reversed(flights))), config
    )

    assert semantic_projection(original)["flights"] == semantic_projection(
        reordered
    )["flights"]
    assert original.fairness_metrics == reordered.fairness_metrics


@pytest.mark.parametrize(
    ("extra_employee", "role"),
    [
        (synthetic_employee("N001"), OperationalRole.NON_RAMP),
        (synthetic_employee("R099", enabled=False), OperationalRole.RAMP_AGENT),
    ],
)
def test_adding_ineligible_or_disabled_employee_does_not_change_schedule(
    extra_employee: Employee, role: OperationalRole
) -> None:
    scenario = small_ready_scenario()
    baseline = optimize_flight_assignments(scenario.day, scenario.config)
    changed_day = replace(
        scenario.day,
        employees=scenario.day.employees + (extra_employee,),
        employee_shifts=scenario.day.employee_shifts
        + (
            synthetic_shift(
                extra_employee.employee_id,
                start=at(7),
                end=at(11),
                role=role,
            ),
        ),
    )
    changed = optimize_flight_assignments(changed_day, scenario.config)

    assert semantic_projection(baseline)["flights"] == semantic_projection(changed)[
        "flights"
    ]
    assert baseline.fairness_metrics == changed.fairness_metrics
    assert baseline.objective_values == changed.objective_values


def test_adding_unavailable_lead_does_not_change_emergency_recovery() -> None:
    ramps = (synthetic_employee("R001"), synthetic_employee("R002"))
    flight = arrival("FX101", at(9))
    base_day = operational_day(
        employees=ramps,
        shifts=tuple(
            synthetic_shift(worker.employee_id, start=at(7), end=at(11))
            for worker in ramps
        ),
        flights=(flight,),
    )
    lead = synthetic_employee("L001")
    with_unavailable_lead = replace(
        base_day,
        employees=base_day.employees + (lead,),
        employee_shifts=base_day.employee_shifts
        + (
            synthetic_shift(
                "L001",
                start=at(12),
                end=at(16),
                role=OperationalRole.RAMP_LEAD,
            ),
        ),
    )
    config = optimizer_config(
        allow_leads_for_minimum_staffing=True,
        solver_time_limit_seconds=2.0,
    )

    baseline = optimize_flight_assignments(base_day, config)
    changed = optimize_flight_assignments(with_unavailable_lead, config)

    assert baseline.flight_results == changed.flight_results
    assert baseline.lead_assignments == changed.lead_assignments == ()
    assert [attempt.critical_shortage_count for attempt in baseline.attempts] == [
        attempt.critical_shortage_count for attempt in changed.attempts
    ]


def test_increasing_solver_time_cannot_worsen_lexicographic_result() -> None:
    short = small_ready_scenario(solver_time_limit_seconds=0.25)
    long = replace(
        short,
        config=replace(short.config, solver_time_limit_seconds=2.0),
    )
    short_result = optimize_flight_assignments(short.day, short.config)
    long_result = optimize_flight_assignments(long.day, long.config)

    shared = min(len(short_result.objective_values), len(long_result.objective_values))
    assert lexicographic_values(long_result)[:shared] >= lexicographic_values(
        short_result
    )[:shared]


def test_express_threshold_changes_only_category_dependent_workload() -> None:
    target = arrival("FX3000", at(9))
    worker = synthetic_employee("R001")
    day = operational_day(
        employees=(worker,),
        shifts=(synthetic_shift("R001", start=at(7), end=at(11)),),
        flights=(target,),
        fixed=(FixedAssignment("R001", target),),
    )
    express_config = optimizer_config(
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
        solver_time_limit_seconds=2.0,
    )
    mainline_config = replace(express_config, express_threshold=4000)

    express = optimize_flight_assignments(day, express_config)
    mainline = optimize_flight_assignments(day, mainline_config)

    assert express.flight_results[0].assigned_employee_ids == mainline.flight_results[
        0
    ].assigned_employee_ids
    assert express.flight_results[0].work_start == mainline.flight_results[0].work_start
    assert express.employee_results[0].adjusted_workload != mainline.employee_results[
        0
    ].adjusted_workload


def _gap_result(*, break_minutes: int, streak_minutes: int):
    first = arrival("FX101", at(8, 10))
    second = arrival("FX102", at(9, 15))  # 35 minutes between work windows
    worker = synthetic_employee("R001")
    day = operational_day(
        employees=(worker,),
        shifts=(synthetic_shift("R001", start=at(7), end=at(11)),),
        flights=(first, second),
        fixed=(
            FixedAssignment("R001", first),
            FixedAssignment("R001", second),
        ),
    )
    config = optimizer_config(
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
        required_break_minutes=break_minutes,
        consecutive_reset_minutes=streak_minutes,
        solver_time_limit_seconds=2.0,
    )
    return optimize_flight_assignments(day, config)


def test_break_threshold_does_not_change_streak_classification() -> None:
    thirty = _gap_result(break_minutes=30, streak_minutes=40)
    forty = _gap_result(break_minutes=40, streak_minutes=40)

    assert thirty.employee_results[0].break_status is BreakStatus.SATISFIED
    assert forty.employee_results[0].break_status is BreakStatus.UNSATISFIED
    assert thirty.employee_results[0].longest_consecutive_streak == (
        forty.employee_results[0].longest_consecutive_streak
    ) == 2


def test_streak_threshold_does_not_change_break_classification() -> None:
    thirty = _gap_result(break_minutes=30, streak_minutes=30)
    forty = _gap_result(break_minutes=30, streak_minutes=40)

    assert thirty.employee_results[0].break_status is (
        forty.employee_results[0].break_status
    ) is BreakStatus.SATISFIED
    assert thirty.employee_results[0].longest_consecutive_streak == 1
    assert forty.employee_results[0].longest_consecutive_streak == 2


def test_increasing_continuity_horizon_only_adds_transition_pairs() -> None:
    flights = (
        arrival("FX101", at(8, 10)),
        arrival("FX102", at(9, 10)),
        arrival("FX103", at(11, 10)),
    )
    worker = synthetic_employee("R001")
    day = operational_day(
        employees=(worker,),
        shifts=(synthetic_shift("R001", start=at(7), end=at(13)),),
        flights=flights,
        fixed=tuple(FixedAssignment("R001", flight) for flight in flights),
    )
    base = optimizer_config(
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
        continuity_horizon_minutes=120,
        solver_time_limit_seconds=2.0,
    )
    expanded = replace(base, continuity_horizon_minutes=180)

    base_result = optimize_flight_assignments(day, base)
    expanded_result = optimize_flight_assignments(day, expanded)
    assert base_result.continuity_metrics is not None
    assert expanded_result.continuity_metrics is not None
    base_pairs = {
        (item.previous_flight, item.next_flight)
        for item in base_result.continuity_metrics.transitions
    }
    expanded_pairs = {
        (item.previous_flight, item.next_flight)
        for item in expanded_result.continuity_metrics.transitions
    }

    assert base_pairs < expanded_pairs


def test_heavy_flag_changes_staffing_limits_but_never_work_window() -> None:
    normal = arrival("FX101", at(9))
    heavy = replace(normal, heavy=True)
    workers = tuple(synthetic_employee(f"R{index:03}") for index in range(5))
    shifts = tuple(
        synthetic_shift(worker.employee_id, start=at(7), end=at(11))
        for worker in workers
    )
    config = optimizer_config(solver_time_limit_seconds=2.0)

    normal_result = optimize_flight_assignments(
        operational_day(employees=workers, shifts=shifts, flights=(normal,)), config
    )
    heavy_result = optimize_flight_assignments(
        operational_day(employees=workers, shifts=shifts, flights=(heavy,)), config
    )

    assert derive_work_window(normal, config) == derive_work_window(heavy, config)
    assert normal_result.flight_results[0].maximum_staff == 4
    assert heavy_result.flight_results[0].maximum_staff == 5
    assert normal_result.flight_results[0].staffing_count == 4
    assert heavy_result.flight_results[0].staffing_count == 5


def test_adding_qualification_never_makes_employee_ineligible() -> None:
    target = arrival("FX101", at(9))
    shift = synthetic_shift("R001", start=at(7), end=at(11))
    plain = synthetic_employee("R001")
    qualified = replace(
        plain,
        qualifications=frozenset({Qualification.PUSH, Qualification.CLOSE_OUT}),
    )

    assert assess_employee_flight_eligibility(
        plain, (shift,), target, OptimizerConfig()
    ).eligible
    assert assess_employee_flight_eligibility(
        qualified, (shift,), target, OptimizerConfig()
    ).eligible


def test_lead_configuration_preserves_ready_pass_one_and_never_runs_pass_two() -> None:
    disabled = small_ready_scenario(leads_enabled=False)
    enabled = small_ready_scenario(leads_enabled=True)

    ordinary = optimize_flight_assignments(disabled.day, disabled.config)
    enabled_result = optimize_flight_assignments(enabled.day, enabled.config)

    assert ordinary.flight_results == enabled_result.flight_results
    assert ordinary.employee_results == enabled_result.employee_results
    assert ordinary.fairness_metrics == enabled_result.fairness_metrics
    assert ordinary.objective_values == enabled_result.objective_values
    assert len(enabled_result.attempts) == 1
    assert enabled_result.lead_assignments == ()


def test_small_cross_layer_result_is_repeatable_with_fixed_seed() -> None:
    scenario = small_ready_scenario(leads_enabled=True)
    first = optimize_flight_assignments(scenario.day, scenario.config)
    second = optimize_flight_assignments(scenario.day, scenario.config)

    assert semantic_projection(first) == semantic_projection(second)
    assert format_optimization_report(first).split("Runtime:")[0] == (
        format_optimization_report(second).split("Runtime:")[0]
    )
