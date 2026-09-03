"""Deterministic synthetic tests for the Milestone 4 CP-SAT optimizer."""

from dataclasses import replace
from datetime import date, datetime, timezone
from math import isfinite

import pytest

from ramp_optimizer import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    FlightType,
    InputValidationError,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    StaffingStatus,
    WarningCode,
    optimize_minimum_staffing,
)


def at(
    hour: int,
    minute: int = 0,
    *,
    day: int = 2,
    aware: bool = False,
) -> datetime:
    return datetime(
        2026,
        9,
        day,
        hour,
        minute,
        tzinfo=timezone.utc if aware else None,
    )


def departure(
    number: str,
    hour: int = 9,
    minute: int = 0,
    *,
    day: int = 2,
    heavy: bool = False,
    gate: str | None = None,
) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute, day=day),
        gate=gate,
        heavy=heavy,
    )


def arrival(number: str, hour: int = 9, minute: int = 0) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute),
    )


def turn(
    arrival_number: str,
    departure_number: str,
    arrival_hour: int,
    arrival_minute: int,
    departure_hour: int,
    departure_minute: int,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=at(arrival_hour, arrival_minute),
        departure_flight_number=departure_number,
        departure_time=at(departure_hour, departure_minute),
    )


def roster(count: int) -> tuple[Employee, ...]:
    return tuple(
        Employee(f"E{index:03}", f"Employee {index:03}")
        for index in range(1, count + 1)
    )


def shifts_for(
    employees: tuple[Employee, ...],
    *,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 13),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> tuple[EmployeeShift, ...]:
    return tuple(
        EmployeeShift(employee.employee_id, start, end, role)
        for employee in employees
    )


def staffed_day(
    flights: tuple[Flight, ...],
    employee_count: int,
    *,
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    employees = roster(employee_count)
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=flights,
        fixed_assignments=fixed,
    )


def counts(result) -> list[int]:
    return [flight_result.staffing_count for flight_result in result.flight_results]


@pytest.mark.parametrize(
    ("employee_count", "expected_count", "minimum_met"),
    [(3, 3, True), (2, 2, False), (0, 0, False)],
)
def test_minimum_staffing_is_recoverable(
    employee_count: int, expected_count: int, minimum_met: bool
) -> None:
    result = optimize_minimum_staffing(
        staffed_day((departure("101"),), employee_count)
    )
    flight_result = result.flight_results[0]

    assert result.status is OptimizationStatus.OPTIMAL
    assert flight_result.staffing_count == expected_count
    assert flight_result.minimum_met is minimum_met
    assert flight_result.minimum_shortfall == max(0, 3 - expected_count)
    assert flight_result.staffing_status is (
        StaffingStatus.MINIMUM_STAFFED
        if minimum_met
        else StaffingStatus.BELOW_MINIMUM
    )
    warning_codes = {warning.code for warning in flight_result.warnings}
    assert (WarningCode.MINIMUM_STAFFING_NOT_MET in warning_codes) is (
        not minimum_met
    )


@pytest.mark.parametrize(
    ("employee_count", "expected"),
    [(6, [3, 3]), (5, [2, 3]), (4, [1, 3])],
)
def test_minimum_objectives_cover_then_distribute_simultaneous_staff(
    employee_count: int, expected: list[int]
) -> None:
    flights = (departure("101"), departure("102"))

    result = optimize_minimum_staffing(staffed_day(flights, employee_count))

    assert sorted(counts(result)) == expected


def test_largest_shortfall_stage_avoids_concentrating_tied_shortages() -> None:
    flights = (departure("101"), departure("102"), departure("103"))

    result = optimize_minimum_staffing(staffed_day(flights, 5))

    assert sorted(counts(result)) == [1, 1, 3]
    assert result.objective_values[4].name == "largest_minimum_shortfall"
    assert result.objective_values[4].value == 2


@pytest.mark.parametrize(
    ("flights", "employee_count", "expected"),
    [
        ((departure("101"),), 4, [4]),
        ((departure("101", heavy=True),), 5, [5]),
        ((departure("101"), departure("102")), 7, [3, 4]),
        ((departure("101"), departure("102")), 8, [4, 4]),
        (
            (departure("101"), departure("102", heavy=True)),
            9,
            [4, 5],
        ),
    ],
)
def test_preferred_staffing_after_minimum_priorities(
    flights: tuple[Flight, ...], employee_count: int, expected: list[int]
) -> None:
    result = optimize_minimum_staffing(staffed_day(flights, employee_count))

    assert sorted(counts(result)) == expected
    assert sum(item.minimum_met for item in result.flight_results) == len(flights)
    for item in result.flight_results:
        assert item.preferred_met is (item.staffing_count == item.preferred_staff)
        assert item.preferred_shortfall == item.preferred_staff - item.staffing_count
        assert item.staffing_status is (
            StaffingStatus.PREFERRED_STAFFED
            if item.preferred_met
            else StaffingStatus.MINIMUM_STAFFED
        )


def test_abundant_candidates_stop_at_derived_normal_and_heavy_maximums() -> None:
    normal = optimize_minimum_staffing(staffed_day((departure("101"),), 8))
    heavy = optimize_minimum_staffing(
        staffed_day((departure("102", heavy=True),), 8)
    )

    assert normal.flight_results[0].staffing_count == 4
    assert normal.flight_results[0].maximum_staff == 4
    assert heavy.flight_results[0].staffing_count == 5
    assert heavy.flight_results[0].maximum_staff == 5


def test_disabled_outside_shift_and_lead_are_never_assigned() -> None:
    target = departure("101")
    employees = (
        Employee("LEGAL", "Legal", qualifications=frozenset()),
        Employee("DISABLED", "Disabled", enabled=False),
        Employee("OUTSIDE", "Outside"),
        Employee("LEAD", "Lead"),
    )
    shifts = (
        EmployeeShift("LEGAL", at(5), at(13), OperationalRole.RAMP_AGENT),
        EmployeeShift("DISABLED", at(5), at(13), OperationalRole.RAMP_AGENT),
        EmployeeShift("OUTSIDE", at(10), at(13), OperationalRole.RAMP_AGENT),
        EmployeeShift("LEAD", at(5), at(13), OperationalRole.RAMP_LEAD),
    )
    day = OperationalDay(
        date(2026, 9, 2), employees=employees, employee_shifts=shifts, flights=(target,)
    )

    result = optimize_minimum_staffing(day)

    assert result.flight_results[0].assigned_employee_ids == ("LEGAL",)


def test_emergency_lead_config_does_not_enable_lead_in_ordinary_optimizer() -> None:
    lead = Employee("L001", "Lead")
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(lead,),
        employee_shifts=(
            EmployeeShift("L001", at(5), at(13), OperationalRole.RAMP_LEAD),
        ),
        flights=(departure("101"),),
    )
    config = replace(OptimizerConfig(), allow_leads_for_minimum_staffing=True)

    result = optimize_minimum_staffing(day, config)

    assert result.flight_results[0].assigned_employee_ids == ()


@pytest.mark.parametrize(
    ("role", "config_field"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_config_enabled_special_role_reaches_optimizer_candidate_set(
    role: OperationalRole, config_field: str
) -> None:
    worker = Employee("E001", "Worker")
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(worker,),
        employee_shifts=(EmployeeShift("E001", at(5), at(13), role),),
        flights=(departure("101"),),
    )

    excluded = optimize_minimum_staffing(day)
    included = optimize_minimum_staffing(
        day, replace(OptimizerConfig(), **{config_field: True})
    )

    assert excluded.flight_results[0].staffing_count == 0
    assert included.flight_results[0].assigned_employee_ids == ("E001",)


def test_employee_without_push_or_close_qualification_can_be_assigned() -> None:
    employees = roster(3)
    assert all(employee.qualifications == frozenset() for employee in employees)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(departure("101"),),
    )

    result = optimize_minimum_staffing(day)

    assert result.flight_results[0].staffing_count == 3


def test_one_employee_cannot_work_overlapping_departures() -> None:
    result = optimize_minimum_staffing(
        staffed_day((departure("101", 9), departure("102", 9, 30)), 1)
    )

    assert sum(counts(result)) == 1


def test_one_employee_may_work_touching_and_separated_flights() -> None:
    touching = optimize_minimum_staffing(
        staffed_day((departure("101", 9), departure("102", 10)), 1)
    )
    separated = optimize_minimum_staffing(
        staffed_day((departure("103", 9), departure("104", 11)), 1)
    )

    assert counts(touching) == [1, 1]
    assert counts(separated) == [1, 1]


@pytest.mark.parametrize(
    "flights",
    [
        (arrival("101", 9), arrival("102", 9, 10)),
        (departure("103", 9), departure("104", 9, 30)),
        (
            turn("105", "106", 8, 50, 9, 30),
            turn("107", "108", 9, 0, 10, 0),
        ),
    ],
)
def test_overlap_constraints_cover_arrivals_departures_and_turns(
    flights: tuple[Flight, Flight]
) -> None:
    result = optimize_minimum_staffing(staffed_day(flights, 1))

    assert sum(counts(result)) == 1


def test_overnight_overlap_is_constrained_with_full_datetimes() -> None:
    overnight_employee = Employee("E001", "Overnight")
    flights = (
        Flight(
            departure_flight_number="101",
            departure_time=at(0, 30, day=3),
        ),
        Flight(
            arrival_flight_number="102",
            arrival_time=at(23, 55),
        ),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(overnight_employee,),
        employee_shifts=(
            EmployeeShift(
                "E001", at(22), at(6, day=3), OperationalRole.RAMP_AGENT
            ),
        ),
        flights=flights,
    )

    result = optimize_minimum_staffing(day)

    assert sum(counts(result)) == 1


def test_fixed_employee_is_constant_counted_and_never_duplicated() -> None:
    target = departure("101")
    employees = roster(5)
    fixed = FixedAssignment("E003", target)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(target,),
        fixed_assignments=(fixed,),
    )

    result = optimize_minimum_staffing(day)
    flight_result = result.flight_results[0]

    assert flight_result.staffing_count == 4
    assert flight_result.fixed_employee_ids == ("E003",)
    assert flight_result.assigned_employee_ids.count("E003") == 1


def test_multiple_fixed_employees_coexist_with_candidates_up_to_maximum() -> None:
    target = departure("101")
    employees = roster(5)
    fixed = (
        FixedAssignment("E001", target),
        FixedAssignment("E002", target),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(target,),
        fixed_assignments=fixed,
    )

    result = optimize_minimum_staffing(day)

    assert result.flight_results[0].fixed_employee_ids == ("E001", "E002")
    assert result.flight_results[0].staffing_count == 4


def test_fixed_staffing_over_maximum_is_rejected_before_solving() -> None:
    target = departure("101")
    employees = roster(5)
    fixed = tuple(FixedAssignment(employee.employee_id, target) for employee in employees)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(target,),
        fixed_assignments=fixed,
    )

    with pytest.raises(InputValidationError) as error:
        optimize_minimum_staffing(day)

    assert any(
        issue.code == "FIXED_ASSIGNMENTS_EXCEED_MAXIMUM_STAFFING"
        for issue in error.value.issues
    )


def test_candidate_conflicting_with_fixed_assignment_is_never_selected() -> None:
    fixed_flight = departure("101", 9)
    target = departure("102", 9, 30)
    employees = roster(2)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(fixed_flight, target),
        fixed_assignments=(FixedAssignment("E001", fixed_flight),),
    )

    result = optimize_minimum_staffing(day)

    assert "E001" not in result.flight_results[1].assigned_employee_ids
    assert result.flight_results[1].assigned_employee_ids == ("E002",)


def test_objective_reporting_matches_partial_schedule() -> None:
    result = optimize_minimum_staffing(staffed_day((departure("101"),), 2))

    assert [objective.name for objective in result.objective_values] == [
        "minimum_covered_flights",
        "minimum_staffed_qualification_compliant_flights",
        "minimum_staffed_individual_qualification_coverage",
        "total_minimum_shortfall",
        "largest_minimum_shortfall",
        "known_unsatisfied_required_breaks",
        "preferred_staffed_flights",
        "total_preferred_shortfall",
        "partial_crew_individual_qualification_coverage",
        "raw_flight_count_spread",
        "total_pairwise_flight_count_difference",
        "total_shift_adjusted_flight_count_deviation",
    ]
    assert [objective.value for objective in result.objective_values] == [
        0,
        0,
        0,
        1,
        1,
        0,
        0,
        2,
        0,
        0,
        0,
        0,
    ]
    assert all(objective.proven_optimal for objective in result.objective_values)
    assert result.status is OptimizationStatus.OPTIMAL
    assert isfinite(result.solver_runtime_seconds)
    assert result.solver_runtime_seconds >= 0


def test_result_preserves_order_facts_and_marks_future_metrics_unevaluated() -> None:
    first = departure("3001", 9, heavy=True, gate="B4")
    second = arrival("102", 11)
    employees = (
        Employee("E003", "Third", frozenset({Qualification.PUSH})),
        Employee("E001", "First"),
        Employee("E002", "Second"),
        Employee("E004", "Fourth"),
        Employee("E005", "Fifth"),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts_for(employees),
        flights=(first, second),
        fixed_assignments=(FixedAssignment("E002", first),),
    )
    original_day = day

    result = optimize_minimum_staffing(day)

    assert tuple(item.flight for item in result.flight_results) == (first, second)
    first_result = result.flight_results[0]
    assert first_result.assigned_employee_ids == tuple(
        employee.employee_id
        for employee in employees
        if employee.employee_id in first_result.assigned_employee_ids
    )
    assert first_result.fixed_employee_ids == ("E002",)
    assert first_result.staffing_count == len(first_result.assigned_employee_ids)
    assert first_result.flight_type is FlightType.DEPARTURE_ONLY
    assert first_result.work_start == at(8)
    assert first_result.work_end == at(9)
    assert first_result.express
    assert first_result.heavy
    assert first_result.flight.gate == "B4"
    assert first_result.push_covered is True
    assert first_result.close_covered is False
    assert tuple(item.employee_id for item in result.employee_results) == tuple(
        employee.employee_id for employee in employees
    )
    assert all(
        item.longest_consecutive_streak is None
        and item.adjusted_workload is None
        for item in result.employee_results
    )
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 5
    assert result.fairness_metrics.total_assignments == 9
    assert result.fairness_metrics.average_flights == 1.8
    assert result.fairness_metrics.highest_flight_count == 2
    assert result.fairness_metrics.lowest_flight_count == 1
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.fairness_metrics.maximum_consecutive_streak is None
    assert result.fairness_metrics.adjusted_workload_spread is None
    assert result.attempts == ()
    assert result.emergency_lead_staffing_used is None
    assert day == original_day


def test_empty_day_and_no_employee_day_return_optimal_results() -> None:
    empty = optimize_minimum_staffing(OperationalDay(date(2026, 9, 2)))
    no_employees = optimize_minimum_staffing(
        OperationalDay(date(2026, 9, 2), flights=(departure("101"),))
    )

    assert empty.status is OptimizationStatus.OPTIMAL
    assert empty.flight_results == ()
    assert [objective.value for objective in empty.objective_values] == [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    assert empty.fairness_metrics is not None
    assert empty.fairness_metrics.participating_employee_count == 0
    assert empty.fairness_metrics.total_assignments == 0
    assert empty.fairness_metrics.average_flights == 0.0
    assert empty.fairness_metrics.highest_flight_count == 0
    assert empty.fairness_metrics.lowest_flight_count == 0
    assert empty.fairness_metrics.flight_count_spread == 0
    assert no_employees.status is OptimizationStatus.OPTIMAL
    assert no_employees.flight_results[0].minimum_shortfall == 3


def test_invalid_inputs_raise_before_model_construction() -> None:
    invalid_flight_day = OperationalDay(date(2026, 9, 2), flights=(Flight(),))
    bad_config = replace(OptimizerConfig(), minimum_staff=0)

    with pytest.raises(InputValidationError):
        optimize_minimum_staffing(invalid_flight_day)
    with pytest.raises(InputValidationError):
        optimize_minimum_staffing(OperationalDay(date(2026, 9, 2)), bad_config)


def test_illegal_fixed_assignment_raises_aggregate_validation_error() -> None:
    target = departure("101")
    disabled = Employee("E001", "Disabled", enabled=False)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(disabled,),
        employee_shifts=(
            EmployeeShift("E001", at(5), at(13), OperationalRole.RAMP_AGENT),
        ),
        flights=(target,),
        fixed_assignments=(FixedAssignment("E001", target),),
    )

    with pytest.raises(InputValidationError) as error:
        optimize_minimum_staffing(day)

    assert any(
        issue.code == "ILLEGAL_FIXED_ASSIGNMENT" for issue in error.value.issues
    )


def test_mixed_datetime_awareness_is_validation_error_not_solver_crash() -> None:
    employee = Employee("E001", "Employee")
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee,),
        employee_shifts=(
            EmployeeShift("E001", at(5), at(13), OperationalRole.RAMP_AGENT),
        ),
        flights=(
            Flight(
                departure_flight_number="101",
                departure_time=at(9, aware=True),
            ),
        ),
    )

    with pytest.raises(InputValidationError) as error:
        optimize_minimum_staffing(day)

    assert any(
        issue.code == "MIXED_DATETIME_AWARENESS" for issue in error.value.issues
    )
