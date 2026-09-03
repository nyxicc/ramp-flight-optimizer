"""Deterministic Milestone 6 required-break tests."""

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
    WarningSeverity,
    build_candidate_assignments,
    optimize_flight_assignments,
)


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def arrival(number: str, hour: int, minute: int = 0, *, day: int = 2) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute, day=day),
    )


def departure(
    number: str, hour: int, minute: int = 0, *, day: int = 2
) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute, day=day),
    )


def employee(
    employee_id: str = "E001",
    *qualifications: Qualification,
    enabled: bool = True,
) -> Employee:
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        frozenset(qualifications),
        enabled,
    )


def shift(
    employee_id: str = "E001",
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def operational_day(
    flights: tuple[Flight, ...],
    employees: tuple[Employee, ...] = (Employee("E001", "Employee E001"),),
    *,
    shifts: tuple[EmployeeShift, ...] | None = None,
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts
        if shifts is not None
        else tuple(shift(worker.employee_id) for worker in employees),
        flights=flights,
        fixed_assignments=fixed,
    )


def break_result(result, employee_id: str = "E001"):
    return next(
        item for item in result.employee_results if item.employee_id == employee_id
    )


def break_warnings(result, employee_id: str = "E001"):
    return tuple(
        warning
        for warning in result.warnings
        if warning.code is WarningCode.REQUIRED_BREAK_NOT_MET
        and warning.employee_id == employee_id
    )


@pytest.mark.parametrize(
    ("second_arrival", "expected"),
    [
        ((9, 40), BreakStatus.SATISFIED),
        ((9, 39), BreakStatus.UNSATISFIED),
        ((9, 10), BreakStatus.UNSATISFIED),
        ((9, 41), BreakStatus.SATISFIED),
    ],
)
def test_break_boundary_semantics(
    second_arrival: tuple[int, int], expected: BreakStatus
) -> None:
    flights = (
        arrival("101", 8, 40),
        arrival("102", *second_arrival),
    )

    result = optimize_flight_assignments(operational_day(flights))

    assert break_result(result).break_status is expected


def test_one_qualifying_gap_among_short_gaps_is_sufficient() -> None:
    flights = (
        arrival("101", 8, 40),
        arrival("102", 9, 30),
        arrival("103", 10, 40),
    )

    result = optimize_flight_assignments(operational_day(flights))

    assert break_result(result).break_status is BreakStatus.SATISFIED


def test_required_break_duration_comes_from_config() -> None:
    flights = (arrival("101", 8, 40), arrival("102", 9, 50))
    config = replace(OptimizerConfig(), required_break_minutes=45)

    result = optimize_flight_assignments(operational_day(flights), config)

    assert break_result(result).break_status is BreakStatus.UNSATISFIED


def test_multiple_short_gaps_are_unsatisfied() -> None:
    flights = (
        arrival("101", 8, 40),
        arrival("102", 9, 30),
        arrival("103", 10, 20),
    )

    result = optimize_flight_assignments(operational_day(flights))

    assert break_result(result).break_status is BreakStatus.UNSATISFIED


@pytest.mark.parametrize("flights", [(), (arrival("101", 12),)])
def test_fewer_than_two_assignments_are_not_evaluable_and_never_warn(
    flights: tuple[Flight, ...],
) -> None:
    result = optimize_flight_assignments(operational_day(flights))
    employee_result = break_result(result)

    assert employee_result.break_status is BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
    assert break_warnings(result) == ()


def test_idle_time_before_and_after_one_assignment_never_counts() -> None:
    worker = employee()
    long_shift = shift(start=at(5), end=at(18))
    result = optimize_flight_assignments(
        operational_day(
            (arrival("101", 12),),
            (worker,),
            shifts=(long_shift,),
        )
    )

    assert break_result(result).break_status is (
        BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
    )


def test_unsatisfied_break_produces_one_employee_level_critical_warning() -> None:
    flights = (arrival("101", 8, 40), arrival("102", 9, 39))

    result = optimize_flight_assignments(operational_day(flights))
    warnings = break_warnings(result)

    assert break_result(result).break_status is BreakStatus.UNSATISFIED
    assert len(warnings) == 1
    assert warnings[0].severity is WarningSeverity.CRITICAL
    assert warnings[0].employee_id == "E001"
    assert warnings[0].arrival_flight_number is None
    assert warnings[0].departure_flight_number is None


def test_satisfied_break_produces_no_break_warning() -> None:
    flights = (arrival("101", 8, 40), arrival("102", 9, 40))

    result = optimize_flight_assignments(operational_day(flights))

    assert break_result(result).break_status is BreakStatus.SATISFIED
    assert break_warnings(result) == ()


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        (arrival("102", 9, 40), BreakStatus.SATISFIED),
        (arrival("102", 9, 39), BreakStatus.UNSATISFIED),
    ],
)
def test_fixed_only_assignments_participate_in_break_evaluation(
    second: Flight, expected: BreakStatus
) -> None:
    first = arrival("101", 8, 40)
    worker = employee()
    fixed = (FixedAssignment("E001", first), FixedAssignment("E001", second))

    result = optimize_flight_assignments(
        operational_day((first, second), (worker,), fixed=fixed)
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert break_result(result).break_status is expected


def test_intervening_fixed_assignment_splits_an_otherwise_qualifying_gap() -> None:
    first = arrival("101", 8, 40)
    intervening = arrival("102", 9, 15)
    last = arrival("103", 10, 10)
    fixed = tuple(
        FixedAssignment("E001", flight)
        for flight in (first, intervening, last)
    )

    result = optimize_flight_assignments(
        operational_day((first, last, intervening), fixed=fixed)
    )

    assert break_result(result).assigned_flights == (first, intervening, last)
    assert break_result(result).break_status is BreakStatus.UNSATISFIED


def test_intervening_selected_assignment_splits_qualifying_fixed_gap() -> None:
    first = arrival("101", 8, 40)
    intervening = arrival("102", 9, 15)
    last = arrival("103", 10, 10)
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )
    fixed = (FixedAssignment("E001", first), FixedAssignment("E001", last))

    result = optimize_flight_assignments(
        operational_day((first, last, intervening), fixed=fixed),
        config,
    )

    assert "E001" in result.flight_results[2].assigned_employee_ids
    assert break_result(result).assigned_flights == (first, intervening, last)
    assert break_result(result).break_status is BreakStatus.UNSATISFIED


def test_independent_shifts_are_never_joined_to_manufacture_break() -> None:
    first = arrival("101", 8, 40)
    second = arrival("102", 10, 10)
    shifts = (
        shift(start=at(8, 30), end=at(9)),
        shift(start=at(10), end=at(10, 30)),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((first, second), shifts=shifts),
        config,
    )

    assert break_result(result).flight_count == 2
    assert break_result(result).break_status is BreakStatus.UNSATISFIED


def test_qualifying_gap_in_one_of_multiple_shifts_satisfies_daily_break() -> None:
    first = arrival("101", 7, 40)
    second = arrival("102", 8, 40)
    third = arrival("103", 12, 40)
    shifts = (
        shift(start=at(7, 30), end=at(9)),
        shift(start=at(12, 30), end=at(13)),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((third, second, first), shifts=shifts),
        config,
    )

    assert break_result(result).break_status is BreakStatus.SATISFIED


def test_overnight_gap_uses_full_datetime_arithmetic() -> None:
    first = departure("101", 23, 30)
    second = departure("102", 1, 0, day=3)
    overnight_shift = shift(start=at(22), end=at(2, day=3))
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((second, first), shifts=(overnight_shift,)),
        config,
    )

    assert break_result(result).assigned_flights == (first, second)
    assert break_result(result).break_status is BreakStatus.SATISFIED


def test_assignment_span_may_touch_both_shift_boundaries() -> None:
    first = arrival("101", 8, 40)
    second = arrival("102", 9, 40)
    exact_shift = shift(start=at(8, 30), end=at(10))

    result = optimize_flight_assignments(
        operational_day((first, second), shifts=(exact_shift,))
    )

    assert break_result(result).break_status is BreakStatus.SATISFIED


def test_break_priority_preserves_minimum_staffed_flight_count() -> None:
    flights = (departure("101", 9), departure("102", 10))
    workers = tuple(employee(f"E{index}") for index in range(1, 7))

    result = optimize_flight_assignments(operational_day(flights, workers))

    assert result.objective_values[0].value == 2
    assert sum(item.minimum_met for item in result.flight_results) == 2


def test_break_priority_preserves_higher_qualification_coverage() -> None:
    flights = (departure("101", 9), departure("102", 10))
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("NONE"),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers),
        config,
    )
    dual_result = break_result(result, "DUAL")

    assert result.objective_values[1].value == 2
    assert result.objective_values[2].value == 4
    assert dual_result.flight_count == 2
    assert dual_result.break_status is BreakStatus.UNSATISFIED


def test_break_priority_preserves_minimum_shortfall_distribution() -> None:
    flights = (departure("101", 9), departure("102", 9))
    workers = tuple(employee(f"E{index}") for index in range(1, 6))

    result = optimize_flight_assignments(operational_day(flights, workers))

    assert sorted(item.staffing_count for item in result.flight_results) == [2, 3]
    assert result.objective_values[3].value == 1
    assert result.objective_values[4].value == 1


def test_tied_operational_schedule_prefers_satisfied_break() -> None:
    first = arrival("101", 8, 40)
    touching = arrival("102", 9, 10)
    qualifying = arrival("103", 9, 40)
    workers = (employee("E001"), employee("E002"))
    fixed = (FixedAssignment("E001", first),)
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((first, touching, qualifying), workers, fixed=fixed),
        config,
    )

    assert break_result(result, "E001").assigned_flights == (first, qualifying)
    assert break_result(result, "E001").break_status is BreakStatus.SATISFIED
    assert break_result(result, "E002").assigned_flights == (touching,)
    assert result.objective_values[5].value == 0


def test_break_optimization_occurs_before_preferred_staffing() -> None:
    first = arrival("101", 8, 40)
    touching = arrival("102", 9, 10)
    workers = (employee("E001"), employee("E002"))
    fixed = (
        FixedAssignment("E001", first),
        FixedAssignment("E002", touching),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
    )

    result = optimize_flight_assignments(
        operational_day((first, touching), workers, fixed=fixed),
        config,
    )

    assert [item.staffing_count for item in result.flight_results] == [1, 1]
    assert all(
        item.break_status is BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
        for item in result.employee_results
    )
    assert result.objective_values[5].value == 0
    assert result.objective_values[6].value == 0


def test_preferred_assignment_cannot_interrupt_protected_qualifying_break() -> None:
    first = arrival("101", 8, 40)
    intervening = arrival("102", 9, 15)
    last = arrival("103", 10, 10)
    workers = (employee("E001"), employee("E002"))
    shifts = (
        shift("E001"),
        shift("E002", start=at(9, 5), end=at(9, 35)),
    )
    fixed = (
        FixedAssignment("E001", first),
        FixedAssignment("E001", last),
        FixedAssignment("E002", intervening),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
    )

    result = optimize_flight_assignments(
        operational_day(
            (first, intervening, last),
            workers,
            shifts=shifts,
            fixed=fixed,
        ),
        config,
    )

    assert result.flight_results[1].assigned_employee_ids == ("E002",)
    assert break_result(result, "E001").break_status is BreakStatus.SATISFIED
    assert result.objective_values[5].value == 0


def test_break_objective_does_not_manufacture_evaluable_participation() -> None:
    first = arrival("101", 8, 40)
    qualifying = arrival("102", 9, 40)
    workers = (employee("E001"), employee("E002"))
    fixed = (FixedAssignment("E001", first),)
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((first, qualifying), workers, fixed=fixed),
        config,
    )

    assert sum(item.flight_count for item in result.employee_results) == 2
    assert result.objective_values[5].value == 0


def test_all_flight_types_participate_in_chronological_break_timing() -> None:
    arriving = arrival("101", 7, 40)
    departing = departure("102", 9, 30)
    turning = Flight(
        arrival_flight_number="201",
        arrival_time=at(10),
        departure_flight_number="202",
        departure_time=at(11),
    )
    config = replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
    )

    result = optimize_flight_assignments(
        operational_day((turning, departing, arriving)),
        config,
    )

    assert break_result(result).assigned_flights == (arriving, departing, turning)
    assert break_result(result).break_status is BreakStatus.SATISFIED


def test_employee_results_have_stable_order_raw_counts_and_unevaluated_fields() -> None:
    express = arrival("3001", 10, 40)
    mainline = arrival("101", 8, 40)
    workers = (employee("E003"), employee("E001"), employee("E002"))

    result = optimize_flight_assignments(
        operational_day((express, mainline), workers)
    )

    assert tuple(item.employee_id for item in result.employee_results) == (
        "E003",
        "E001",
        "E002",
    )
    for item in result.employee_results:
        assert item.assigned_flights == (mainline, express)
        assert item.flight_count == 2
        assert item.mainline_flight_count == 1
        assert item.express_flight_count == 1
        assert item.three_person_flight_count == 2
        assert item.longest_consecutive_streak is None
        assert item.adjusted_workload is None


def test_disabled_leads_and_non_ramp_workers_have_no_employee_results() -> None:
    workers = (
        employee("RAMP"),
        employee("DISABLED", enabled=False),
        employee("LEAD"),
        employee("OTHER"),
    )
    shifts = (
        shift("RAMP"),
        shift("DISABLED"),
        shift("LEAD", role=OperationalRole.RAMP_LEAD),
        shift("OTHER", role=OperationalRole.NON_RAMP),
    )

    result = optimize_flight_assignments(
        operational_day((), workers, shifts=shifts)
    )

    assert tuple(item.employee_id for item in result.employee_results) == ("RAMP",)
    assert result.employee_results[0].break_status is (
        BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
    )


def test_flight_and_break_warnings_coexist_in_deterministic_order() -> None:
    flights = (departure("101", 9), departure("102", 10))

    result = optimize_flight_assignments(operational_day(flights))
    warning_codes = tuple(warning.code for warning in result.warnings)

    assert warning_codes == (
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        WarningCode.REQUIRED_BREAK_NOT_MET,
    )


def test_seeded_repeated_runs_have_equivalent_break_results() -> None:
    flights = tuple(
        arrival(str(100 + index), 7 + index, 0)
        for index in range(4)
    )
    workers = tuple(employee(f"E{index}") for index in range(4))
    day = operational_day(flights, workers)

    first = optimize_flight_assignments(day)
    second = optimize_flight_assignments(day)

    assert first.flight_results == second.flight_results
    assert first.employee_results == second.employee_results
    assert first.objective_values == second.objective_values
    assert first.warnings == second.warnings


def test_exact_gap_indicator_rejects_assigned_intervening_flight() -> None:
    first = arrival("101", 8, 40)
    intervening = arrival("102", 9, 15)
    last = arrival("103", 10, 10)
    day = operational_day((first, intervening, last))
    config = OptimizerConfig()
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
    )
    for decision in model_data.decisions.values():
        model_data.model.add(decision == 1)
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    outer_gap = model_data.break_gap_variables[(0, 0, 2)]
    achieved = model_data.break_achieved[0]
    assert achieved is not None
    assert solver.value(outer_gap) == 0
    assert solver.value(achieved) == 0


def test_later_unknown_after_break_stage_preserves_feasible_schedule(
    monkeypatch,
) -> None:
    flights = (arrival("101", 8, 40), arrival("102", 9, 40))
    day = operational_day(flights)
    config = OptimizerConfig()
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
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
        return real_solver_type() if calls <= 6 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)

    status, solver, objectives = optimizer_module._solve_lexicographically(
        model_data,
        config,
        optimizer_module.monotonic(),
    )

    assert status is OptimizationStatus.FEASIBLE
    assert isinstance(solver, real_solver_type)
    assert len(objectives) == 7
    assert all(item.proven_optimal for item in objectives[:6])
    assert objectives[6].proven_optimal is False
    achieved = model_data.break_achieved[0]
    assert achieved is not None
    assert solver.value(achieved) == 1


def test_empty_day_and_no_employee_day_remain_valid() -> None:
    empty = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))
    no_employees = optimize_flight_assignments(
        OperationalDay(date(2026, 9, 2), flights=(arrival("101", 9),))
    )

    assert empty.status is OptimizationStatus.OPTIMAL
    assert empty.flight_results == ()
    assert empty.employee_results == ()
    assert no_employees.status is OptimizationStatus.OPTIMAL
    assert no_employees.employee_results == ()


def test_moderate_synthetic_day_break_model_completes_within_solver_budget() -> None:
    start = at(7)
    flights = tuple(
        Flight(
            arrival_flight_number=str(100 + index),
            arrival_time=start + timedelta(minutes=45 * index),
        )
        for index in range(12)
    )
    workers = tuple(employee(f"E{index:02}") for index in range(10))
    config = replace(OptimizerConfig(), solver_time_limit_seconds=5.0)

    result = optimize_flight_assignments(
        operational_day(flights, workers),
        config,
    )

    assert result.status in {OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE}
    assert len(result.flight_results) == 12
    assert len(result.employee_results) == 10
    assert result.solver_runtime_seconds <= 10.0
