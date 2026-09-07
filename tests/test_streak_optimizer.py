"""Deterministic Milestone 10 consecutive-flight streak tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    BreakStatus,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    WarningCode,
    build_candidate_assignments,
    optimize_flight_assignments,
    validate_config,
)


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def arrival_from_start(number: str, start: datetime) -> Flight:
    """Create a 30-minute arrival work window beginning at ``start``."""

    return Flight(
        arrival_flight_number=number,
        arrival_time=start + timedelta(minutes=10),
    )


def departure_from_start(number: str, start: datetime) -> Flight:
    """Create a 60-minute departure work window beginning at ``start``."""

    return Flight(
        departure_flight_number=number,
        departure_time=start + timedelta(minutes=60),
    )


def employee(
    employee_id: str,
    *,
    enabled: bool = True,
    qualified: bool = True,
) -> Employee:
    qualifications = (
        frozenset({Qualification.PUSH, Qualification.CLOSE_OUT})
        if qualified
        else frozenset()
    )
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        qualifications,
        enabled,
    )


def shift(
    employee_id: str,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def config(**changes) -> OptimizerConfig:
    values = {
        "minimum_staff": 1,
        "normal_preferred_staff": 1,
        "heavy_preferred_staff": 1,
    }
    values.update(changes)
    return replace(OptimizerConfig(), **values)


def day_for(
    flights: tuple[Flight, ...],
    employees: tuple[Employee, ...],
    *,
    shifts: tuple[EmployeeShift, ...] | None = None,
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=(
            shifts
            if shifts is not None
            else tuple(shift(worker.employee_id) for worker in employees)
        ),
        flights=flights,
        fixed_assignments=fixed,
    )


def fixed_sequence_result(
    starts: tuple[datetime, ...],
    *,
    active_config: OptimizerConfig | None = None,
    employee_shifts: tuple[EmployeeShift, ...] | None = None,
):
    worker = employee("E1")
    flights = tuple(
        arrival_from_start(str(100 + index), start)
        for index, start in enumerate(starts)
    )
    return optimize_flight_assignments(
        day_for(
            flights,
            (worker,),
            shifts=employee_shifts,
            fixed=tuple(FixedAssignment("E1", flight) for flight in flights),
        ),
        active_config or config(),
    )


def by_employee(result) -> dict[str, object]:
    return {item.employee_id: item for item in result.employee_results}


def test_no_assignments_report_zero_streak_and_zero_participant_maximum() -> None:
    result = optimize_flight_assignments(
        day_for((), (employee("IDLE"),)),
        config(),
    )

    assert result.employee_results[0].longest_consecutive_streak == 0
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 0
    assert result.fairness_metrics.maximum_consecutive_streak == 0
    assert result.objective_values[11].value == 0
    assert result.objective_values[12].value == 0


@pytest.mark.parametrize("movement_type", ["arrival", "departure", "turn"])
def test_one_movement_counts_as_one_streak_entry(movement_type: str) -> None:
    start = at(8)
    if movement_type == "arrival":
        flight = arrival_from_start("101", start)
    elif movement_type == "departure":
        flight = departure_from_start("201", start)
    else:
        flight = Flight(
            arrival_flight_number="301",
            arrival_time=start + timedelta(minutes=10),
            departure_flight_number="302",
            departure_time=start + timedelta(minutes=60),
        )

    result = optimize_flight_assignments(
        day_for(
            (flight,),
            (employee("E1"),),
            fixed=(FixedAssignment("E1", flight),),
        ),
        config(),
    )

    assert result.employee_results[0].flight_count == 1
    assert result.employee_results[0].longest_consecutive_streak == 1
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.maximum_consecutive_streak == 1


@pytest.mark.parametrize(
    ("gap_minutes", "expected_streak"),
    [(0, 2), (39, 2), (40, 1), (41, 1)],
    ids=("touching", "reset-minus-one", "exact-reset", "reset-plus-one"),
)
def test_default_reset_boundary_is_strict(
    gap_minutes: int,
    expected_streak: int,
) -> None:
    first_start = at(8)
    result = fixed_sequence_result(
        (
            first_start,
            first_start + timedelta(minutes=30 + gap_minutes),
        ),
    )

    assert result.employee_results[0].longest_consecutive_streak == expected_streak


@pytest.mark.parametrize("reset_minutes", [30, 45, 60])
def test_nondefault_reset_values_honor_both_boundary_sides(
    reset_minutes: int,
) -> None:
    first_start = at(8)
    below = fixed_sequence_result(
        (
            first_start,
            first_start + timedelta(minutes=30 + reset_minutes - 1),
        ),
        active_config=config(consecutive_reset_minutes=reset_minutes),
    )
    exact = fixed_sequence_result(
        (
            first_start,
            first_start + timedelta(minutes=30 + reset_minutes),
        ),
        active_config=config(consecutive_reset_minutes=reset_minutes),
    )

    assert below.employee_results[0].longest_consecutive_streak == 2
    assert exact.employee_results[0].longest_consecutive_streak == 1


def test_long_gap_splits_streaks_and_largest_run_is_returned() -> None:
    result = fixed_sequence_result(
        (at(8), at(8, 50), at(9, 40), at(11), at(11, 50)),
        active_config=config(required_break_minutes=10),
    )

    assert result.employee_results[0].longest_consecutive_streak == 3
    assert result.employee_results[0].flight_count == 5
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.maximum_consecutive_streak == 3


def test_overnight_assignments_in_one_shift_continue_normally() -> None:
    overnight_shift = (shift("E1", at(23), at(2, day=3)),)
    result = fixed_sequence_result(
        (at(23, 30), at(0, 20, day=3)),
        employee_shifts=overnight_shift,
    )

    assert result.employee_results[0].longest_consecutive_streak == 2
    assert tuple(
        item.arrival_time for item in result.employee_results[0].assigned_flights
    ) == (at(23, 40), at(0, 30, day=3))


def test_separate_shifts_reset_even_with_a_short_clock_gap() -> None:
    separate_shifts = (
        shift("E1", at(8), at(8, 30)),
        shift("E1", at(8, 50), at(9, 20)),
    )
    result = fixed_sequence_result(
        (at(8), at(8, 50)),
        employee_shifts=separate_shifts,
    )

    assert result.employee_results[0].longest_consecutive_streak == 1


def test_fixed_only_and_mixed_fixed_selected_assignments_are_exact() -> None:
    fixed_only = fixed_sequence_result(
        (at(8), at(8, 50)),
        active_config=config(required_break_minutes=10),
    )
    first = arrival_from_start("201", at(8))
    second = arrival_from_start("202", at(8, 30))
    third = arrival_from_start("203", at(9))
    mixed = optimize_flight_assignments(
        day_for(
            (first, second, third),
            (employee("E1"),),
            fixed=(
                FixedAssignment("E1", first),
                FixedAssignment("E1", third),
            ),
        ),
        config(required_break_minutes=10),
    )

    assert fixed_only.employee_results[0].longest_consecutive_streak == 2
    assert mixed.employee_results[0].longest_consecutive_streak == 3
    assert mixed.flight_results[0].fixed_employee_ids == ("E1",)
    assert mixed.flight_results[1].assigned_employee_ids == ("E1",)
    assert mixed.flight_results[2].fixed_employee_ids == ("E1",)


@pytest.mark.parametrize("fixed_middle", [False, True])
def test_actual_immediate_predecessor_skips_no_selected_assignment(
    fixed_middle: bool,
) -> None:
    flights = tuple(
        arrival_from_start(str(300 + index), at(8) + timedelta(minutes=30 * index))
        for index in range(3)
    )
    fixed = (
        (FixedAssignment("E1", flights[1]),) if fixed_middle else ()
    )
    operational_day = day_for(flights, (employee("E1"),), fixed=fixed)
    active_config = config()
    model_data = optimizer_module._build_model(
        operational_day,
        active_config,
        build_candidate_assignments(operational_day, active_config),
    )
    for decision in model_data.decisions.values():
        model_data.model.add(decision == 1)
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    assert solver.value(model_data.streak_predecessor_arcs[(0, 0, 2)]) == 0
    assert solver.value(model_data.streak_predecessor_arcs[(0, 1, 2)]) == 1
    assert solver.value(model_data.streak_run_lengths[(0, 2)]) == 3
    assert solver.value(model_data.employee_longest_streaks[0]) == 3


def four_choice_flights(prefix: int = 400) -> tuple[Flight, ...]:
    return tuple(
        arrival_from_start(
            str(prefix + index),
            at(9) + timedelta(minutes=50 * index),
        )
        for index in range(4)
    )


def test_equal_raw_counts_prefer_the_smaller_global_maximum_streak() -> None:
    flights = four_choice_flights()
    workers = (employee("A"), employee("B"))
    result = optimize_flight_assignments(
        day_for(
            flights,
            workers,
            fixed=(
                FixedAssignment("A", flights[0]),
                FixedAssignment("B", flights[3]),
            ),
        ),
        config(required_break_minutes=10),
    )

    assert [item.flight_count for item in result.employee_results] == [2, 2]
    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert result.flight_results[2].assigned_employee_ids == ("A",)
    assert [
        item.longest_consecutive_streak for item in result.employee_results
    ] == [1, 1]
    assert result.objective_values[9].value == 0
    assert result.objective_values[10].value == 0
    assert result.objective_values[11].value == 1


def test_streak_fairness_precedes_adjusted_workload_refinement() -> None:
    starts = tuple(at(9) + timedelta(minutes=50 * index) for index in range(4))
    flights = (
        arrival_from_start("701", starts[0]),
        arrival_from_start("3701", starts[1]),
        arrival_from_start("702", starts[2]),
        arrival_from_start("3702", starts[3]),
    )
    result = optimize_flight_assignments(
        day_for(
            flights,
            (employee("A"), employee("B")),
            fixed=(
                FixedAssignment("A", flights[0]),
                FixedAssignment("B", flights[3]),
            ),
        ),
        config(required_break_minutes=10),
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert result.flight_results[2].assigned_employee_ids == ("A",)
    assert [
        item.longest_consecutive_streak for item in result.employee_results
    ] == [1, 1]
    assert [item.adjusted_workload for item in result.employee_results] == [
        2.0,
        1.6,
    ]
    assert result.objective_values[11].value == 1
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.adjusted_workload_spread == 0.4


def test_secondary_streak_stage_reduces_other_employee_longest_runs() -> None:
    fixed_run = tuple(
        arrival_from_start(str(500 + index), at(6) + timedelta(minutes=30 * index))
        for index in range(3)
    )
    choices = four_choice_flights(510)
    flights = fixed_run + choices
    workers = (employee("C"), employee("A"), employee("B"))
    shifts = (
        shift("C", at(5, 30), at(8)),
        shift("A", at(8, 30), at(13)),
        shift("B", at(8, 30), at(13)),
    )
    result = optimize_flight_assignments(
        day_for(
            flights,
            workers,
            shifts=shifts,
            fixed=tuple(FixedAssignment("C", flight) for flight in fixed_run)
            + (
                FixedAssignment("A", choices[0]),
                FixedAssignment("B", choices[3]),
            ),
        ),
        config(required_break_minutes=10),
    )
    results = by_employee(result)

    assert result.objective_values[11].value == 3
    assert result.objective_values[12].value == 5
    assert results["C"].longest_consecutive_streak == 3
    assert results["A"].longest_consecutive_streak == 1
    assert results["B"].longest_consecutive_streak == 1
    assert result.flight_results[4].assigned_employee_ids == ("B",)
    assert result.flight_results[5].assigned_employee_ids == ("A",)


def test_raw_count_fairness_wins_even_when_unequal_counts_have_lower_streak() -> None:
    early = arrival_from_start("601", at(6))
    anchor = arrival_from_start("602", at(9))
    target = arrival_from_start("603", at(9, 50))
    late = arrival_from_start("604", at(14))
    flights = (early, anchor, target, late)
    workers = (employee("A"), employee("B"))
    common_fixed = (
        FixedAssignment("A", early),
        FixedAssignment("B", anchor),
        FixedAssignment("A", late),
    )
    result = optimize_flight_assignments(
        day_for(flights, workers, fixed=common_fixed),
        config(required_break_minutes=10),
    )
    unequal = optimize_flight_assignments(
        day_for(
            flights,
            workers,
            fixed=common_fixed + (FixedAssignment("A", target),),
        ),
        config(required_break_minutes=10),
    )

    assert result.flight_results[2].assigned_employee_ids == ("B",)
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.flight_count_spread == 0
    assert result.fairness_metrics.maximum_consecutive_streak == 2
    assert unequal.fairness_metrics is not None
    assert unequal.fairness_metrics.flight_count_spread == 2
    assert unequal.fairness_metrics.maximum_consecutive_streak == 1


def test_break_and_streak_thresholds_are_independent() -> None:
    first_start = at(8)
    starts = (first_start, first_start + timedelta(minutes=65))
    default = fixed_sequence_result(starts)
    longer_break = fixed_sequence_result(
        starts,
        active_config=config(required_break_minutes=45),
    )
    shorter_reset = fixed_sequence_result(
        starts,
        active_config=config(consecutive_reset_minutes=30),
    )

    assert default.employee_results[0].break_status is BreakStatus.SATISFIED
    assert default.employee_results[0].longest_consecutive_streak == 2
    assert longer_break.employee_results[0].break_status is BreakStatus.UNSATISFIED
    assert longer_break.employee_results[0].longest_consecutive_streak == 2
    assert shorter_reset.employee_results[0].break_status is BreakStatus.SATISFIED
    assert shorter_reset.employee_results[0].longest_consecutive_streak == 1


def test_long_qualified_streak_remains_legal_and_preserves_operational_priority(
) -> None:
    flights = tuple(
        departure_from_start(str(700 + index), at(8 + index))
        for index in range(3)
    )
    workers = (employee("QUALIFIED"), employee("UNQUALIFIED", qualified=False))
    result = optimize_flight_assignments(
        day_for(flights, workers),
        config(),
    )
    results = by_employee(result)

    assert result.status is OptimizationStatus.OPTIMAL
    assert all(
        item.assigned_employee_ids == ("QUALIFIED",)
        for item in result.flight_results
    )
    assert [item.value for item in result.objective_values[:3]] == [3, 3, 6]
    assert results["QUALIFIED"].longest_consecutive_streak == 3
    assert set(warning.code for warning in result.warnings) == {
        WarningCode.REQUIRED_BREAK_NOT_MET
    }


def test_preferred_staffing_is_not_sacrificed_and_no_streak_warning_is_added() -> None:
    flights = (
        arrival_from_start("801", at(8)),
        arrival_from_start("802", at(8, 40)),
    )
    workers = (employee("A"), employee("B"))
    active_config = config(
        minimum_staff=1,
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
        required_break_minutes=10,
    )
    result = optimize_flight_assignments(day_for(flights, workers), active_config)

    assert all(item.staffing_count == 2 for item in result.flight_results)
    assert result.objective_values[6].value == 2
    assert all(
        item.longest_consecutive_streak == 2
        for item in result.employee_results
    )
    assert set(warning.code for warning in result.warnings) <= {
        WarningCode.REQUIRED_BREAK_NOT_MET
    }


def test_streak_optimization_does_not_add_or_overstaff_assignments() -> None:
    flights = tuple(
        arrival_from_start(str(900 + index), at(8 + index))
        for index in range(3)
    )
    result = optimize_flight_assignments(
        day_for(flights, tuple(employee(worker_id) for worker_id in ("A", "B", "C"))),
        config(),
    )

    assert [item.staffing_count for item in result.flight_results] == [1, 1, 1]
    assert sum(item.flight_count for item in result.employee_results) == 3


def test_population_excludes_disabled_leads_and_nonordinary_roles() -> None:
    workers = (
        employee("RAMP"),
        employee("DISABLED", enabled=False),
        employee("LEAD"),
        employee("NONRAMP"),
        employee("UNKNOWN"),
    )
    shifts = (
        shift("RAMP"),
        shift("DISABLED"),
        shift("LEAD", role=OperationalRole.RAMP_LEAD),
        shift("NONRAMP", role=OperationalRole.NON_RAMP),
        shift("UNKNOWN", role=OperationalRole.UNKNOWN),
    )
    flight = arrival_from_start("1001", at(9))
    result = optimize_flight_assignments(
        day_for((flight,), workers, shifts=shifts),
        config(),
    )

    assert tuple(item.employee_id for item in result.employee_results) == ("RAMP",)
    assert result.employee_results[0].longest_consecutive_streak == 1
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1


@pytest.mark.parametrize(
    ("role", "flag"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_configured_ordinary_roles_participate_in_streak_fairness(
    role: OperationalRole,
    flag: str,
) -> None:
    worker = employee("CONFIGURED")
    flight = arrival_from_start("1101", at(9))
    result = optimize_flight_assignments(
        day_for((flight,), (worker,), shifts=(shift("CONFIGURED", role=role),)),
        config(**{flag: True}),
    )

    assert result.employee_results[0].employee_id == "CONFIGURED"
    assert result.employee_results[0].longest_consecutive_streak == 1
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1


@pytest.mark.parametrize("invalid_value", [0, -1, True])
def test_consecutive_reset_minutes_requires_a_positive_nonboolean_integer(
    invalid_value: int,
) -> None:
    issues = validate_config(
        replace(OptimizerConfig(), consecutive_reset_minutes=invalid_value)
    )

    assert any(
        issue.path == "config.consecutive_reset_minutes" for issue in issues
    )


def test_repeated_seeded_runs_have_equivalent_streak_outputs() -> None:
    flights = four_choice_flights(1200)
    operational_day = day_for(
        flights,
        (employee("A"), employee("B")),
        fixed=(
            FixedAssignment("A", flights[0]),
            FixedAssignment("B", flights[3]),
        ),
    )
    active_config = config(required_break_minutes=10)

    first = optimize_flight_assignments(operational_day, active_config)
    second = optimize_flight_assignments(operational_day, active_config)

    assert first.flight_results == second.flight_results
    assert first.employee_results == second.employee_results
    assert first.fairness_metrics == second.fairness_metrics
    assert first.continuity_metrics == second.continuity_metrics
    assert first.objective_values == second.objective_values


@pytest.mark.parametrize(
    ("completed_stages", "interrupted_stage"),
    [(11, 12), (12, 13), (14, 15)],
    ids=("first-streak-stage", "secondary-streak-stage", "later-workload-stage"),
)
def test_unknown_after_a_proven_stage_preserves_feasible_streak_results(
    monkeypatch,
    completed_stages: int,
    interrupted_stage: int,
) -> None:
    flights = four_choice_flights(1300)
    operational_day = day_for(
        flights,
        (employee("A"), employee("B")),
        fixed=(
            FixedAssignment("A", flights[0]),
            FixedAssignment("B", flights[3]),
        ),
    )
    real_solver_type = cp_model.CpSolver
    calls = 0

    class UnknownSolver:
        def __init__(self) -> None:
            self.parameters = type("Parameters", (), {})()

        def solve(self, model) -> cp_model.CpSolverStatus:
            return cp_model.UNKNOWN

    def solver_factory():
        nonlocal calls
        calls += 1
        return real_solver_type() if calls <= completed_stages else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)
    result = optimize_flight_assignments(
        operational_day,
        config(required_break_minutes=10),
    )

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == interrupted_stage
    assert all(
        objective.proven_optimal
        for objective in result.objective_values[:completed_stages]
    )
    assert result.objective_values[-1].stage == interrupted_stage
    assert result.objective_values[-1].proven_optimal is False
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.maximum_consecutive_streak == max(
        item.longest_consecutive_streak for item in result.employee_results
    )
    if completed_stages >= 12:
        assert (
            result.fairness_metrics.maximum_consecutive_streak
            == result.objective_values[11].value
        )
    if completed_stages >= 13:
        assert result.objective_values[12].proven_optimal


def test_moderate_synthetic_day_completes_all_streak_stages() -> None:
    flights = tuple(
        arrival_from_start(
            str(1400 + index),
            at(7) + timedelta(minutes=50 * index),
        )
        for index in range(12)
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    active_config = config(
        required_break_minutes=10,
        solver_time_limit_seconds=8.0,
    )

    result = optimize_flight_assignments(day_for(flights, workers), active_config)

    assert result.status is OptimizationStatus.OPTIMAL
    assert len(result.objective_values) == 17
    assert all(item.proven_optimal for item in result.objective_values)
    assert sorted(item.flight_count for item in result.employee_results) == [3] * 4
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.maximum_consecutive_streak == 1
    assert result.objective_values[11].value == 1
    assert result.objective_values[12].value == 4
