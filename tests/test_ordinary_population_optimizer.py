"""Regression tests for the configured ordinary-assignment population."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from ramp_optimizer import (
    BreakStatus,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    optimize_flight_assignments,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def arrival(number: str, hour: int, minute: int = 0) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute),
    )


def one_person_config(**changes: object) -> OptimizerConfig:
    values: dict[str, object] = {
        "minimum_staff": 1,
        "normal_preferred_staff": 1,
        "heavy_preferred_staff": 1,
    }
    values.update(changes)
    return replace(OptimizerConfig(), **values)


def day_for(
    employees: tuple[Employee, ...],
    shifts: tuple[EmployeeShift, ...],
    flights: tuple[Flight, ...] = (),
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    return OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts,
        flights=flights,
        fixed_assignments=fixed,
    )


@pytest.mark.parametrize(
    ("role", "config_change"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_enabled_configured_role_has_results_metrics_breaks_and_fixed_work(
    role: OperationalRole,
    config_change: str,
) -> None:
    worker = Employee("E1", "Configured worker")
    flights = (arrival("101", 9), arrival("102", 10, 30))
    operational_day = day_for(
        (worker,),
        (EmployeeShift("E1", at(8), at(16), role),),
        flights,
        (FixedAssignment("E1", flights[0]),),
    )

    result = optimize_flight_assignments(
        operational_day,
        one_person_config(**{config_change: True}),
    )

    assert len(result.employee_results) == 1
    employee_result = result.employee_results[0]
    assert employee_result.employee_id == "E1"
    assert employee_result.assigned_flights == flights
    assert employee_result.flight_count == 2
    assert employee_result.scheduled_shift_minutes == 480
    assert employee_result.break_status is BreakStatus.SATISFIED
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1
    assert result.fairness_metrics.total_assignments == 2
    assert result.fairness_metrics.total_participating_shift_minutes == 480
    assert result.objective_values[9].value == 0
    assert result.objective_values[10].value == 0
    assert result.flight_results[0].fixed_employee_ids == ("E1",)


@pytest.mark.parametrize(
    "role",
    [OperationalRole.TRAINEE, OperationalRole.POSSIBLE_RAMP_SUPPORT],
)
def test_configured_roles_remain_excluded_when_disabled(
    role: OperationalRole,
) -> None:
    result = optimize_flight_assignments(
        day_for(
            (Employee("E1", "Disabled role"),),
            (EmployeeShift("E1", at(8), at(16), role),),
        ),
        one_person_config(),
    )

    assert result.employee_results == ()
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 0
    assert result.fairness_metrics.total_assignments == 0
    assert result.fairness_metrics.total_participating_shift_minutes == 0


@pytest.mark.parametrize("allow_leads", [False, True])
def test_leads_remain_outside_ordinary_population(allow_leads: bool) -> None:
    result = optimize_flight_assignments(
        day_for(
            (Employee("L1", "Lead"),),
            (
                EmployeeShift(
                    "L1", at(8), at(16), OperationalRole.RAMP_LEAD
                ),
            ),
        ),
        one_person_config(allow_leads_for_minimum_staffing=allow_leads),
    )

    assert result.employee_results == ()
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 0


@pytest.mark.parametrize(
    ("allow_trainees", "allow_support", "expected_minutes"),
    [
        (False, False, 240),
        (True, False, 360),
        (False, True, 330),
        (True, True, 450),
    ],
)
def test_mixed_role_shift_minutes_use_each_enabled_interval_once(
    allow_trainees: bool,
    allow_support: bool,
    expected_minutes: int,
) -> None:
    worker = Employee("E1", "Mixed role")
    result = optimize_flight_assignments(
        day_for(
            (worker,),
            (
                EmployeeShift(
                    "E1", at(6), at(10), OperationalRole.RAMP_AGENT
                ),
                EmployeeShift("E1", at(10), at(12), OperationalRole.TRAINEE),
                EmployeeShift(
                    "E1",
                    at(12),
                    at(13, 30),
                    OperationalRole.POSSIBLE_RAMP_SUPPORT,
                ),
            ),
            (arrival("101", 9),),
        ),
        one_person_config(
            allow_trainees_for_assignments=allow_trainees,
            allow_possible_ramp_support_for_assignments=allow_support,
        ),
    )

    assert result.employee_results[0].scheduled_shift_minutes == expected_minutes
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == (
        expected_minutes
    )


@pytest.mark.parametrize(
    ("role", "config_change"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_enabled_configured_role_participates_in_raw_and_shift_fairness(
    role: OperationalRole,
    config_change: str,
) -> None:
    flights = (arrival("101", 9), arrival("102", 10), arrival("103", 11))
    result = optimize_flight_assignments(
        day_for(
            (Employee("LONG", "Long"), Employee("SHORT", "Short")),
            (
                EmployeeShift("LONG", at(8), at(16), role),
                EmployeeShift(
                    "SHORT", at(8), at(12), OperationalRole.RAMP_AGENT
                ),
            ),
            flights,
        ),
        one_person_config(**{config_change: True}),
    )

    counts = {
        item.employee_id: item.flight_count for item in result.employee_results
    }
    assert counts == {"LONG": 2, "SHORT": 1}
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 2
    assert result.fairness_metrics.total_assignments == 3
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.fairness_metrics.total_participating_shift_minutes == 720
    assert result.objective_values[9].value == 1
    assert result.objective_values[10].value == 1
    assert result.objective_values[11].value == 0
