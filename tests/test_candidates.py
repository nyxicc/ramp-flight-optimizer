"""Tests for validated candidate preprocessing."""

from dataclasses import replace
from datetime import date, datetime

import pytest
from ortools.sat.python import cp_model

from ramp_optimizer import (
    CandidateAssignment,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    InputValidationError,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    build_candidate_assignments,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def employee(employee_id: str, *, enabled: bool = True) -> Employee:
    return Employee(employee_id, f"Employee {employee_id}", enabled=enabled)


def shift(
    employee_id: str,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 13),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def flight(number: str, departure_hour: int, minute: int = 0) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(departure_hour, minute),
    )


def test_candidate_order_is_employee_then_flight_input_order() -> None:
    employees = (employee("E002"), employee("E001"))
    flights = (flight("101", 9), flight("102", 10))
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=(shift("E002"), shift("E001")),
        flights=flights,
    )

    candidates = build_candidate_assignments(day, OptimizerConfig())

    assert candidates == (
        CandidateAssignment("E002", flights[0]),
        CandidateAssignment("E002", flights[1]),
        CandidateAssignment("E001", flights[0]),
        CandidateAssignment("E001", flights[1]),
    )


def test_only_eligible_non_fixed_pairs_are_candidates() -> None:
    target_fixed = flight("101", 9)
    endpoint_target = flight("102", 10)
    employees = (
        employee("ELIGIBLE"),
        employee("DISABLED", enabled=False),
        employee("OUTSIDE"),
        employee("LEAD"),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=(
            shift("ELIGIBLE"),
            shift("DISABLED"),
            shift("OUTSIDE", at(10), at(13)),
            shift("LEAD", role=OperationalRole.RAMP_LEAD),
        ),
        flights=(target_fixed, endpoint_target),
        fixed_assignments=(FixedAssignment("ELIGIBLE", target_fixed),),
    )

    candidates = build_candidate_assignments(day, OptimizerConfig())

    assert candidates == (CandidateAssignment("ELIGIBLE", endpoint_target),)


def test_other_overlapping_fixed_flight_removes_candidate() -> None:
    fixed_flight = flight("101", 9)
    overlapping_target = flight("102", 9, 30)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee("E001"),),
        employee_shifts=(shift("E001"),),
        flights=(fixed_flight, overlapping_target),
        fixed_assignments=(FixedAssignment("E001", fixed_flight),),
    )

    assert build_candidate_assignments(day, OptimizerConfig()) == ()


def test_explicit_lead_override_builds_lead_candidate() -> None:
    target = flight("101", 9)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee("L001"),),
        employee_shifts=(
            shift("L001", role=OperationalRole.RAMP_LEAD),
        ),
        flights=(target,),
    )

    assert build_candidate_assignments(day, OptimizerConfig()) == ()
    assert build_candidate_assignments(
        day, OptimizerConfig(), include_leads=True
    ) == (CandidateAssignment("L001", target),)


@pytest.mark.parametrize(
    ("role", "config_field", "override_name"),
    [
        (
            OperationalRole.TRAINEE,
            "allow_trainees_for_assignments",
            "allow_trainees",
        ),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
            "allow_possible_ramp_support",
        ),
    ],
)
def test_candidate_generation_uses_configured_role_policy(
    role: OperationalRole, config_field: str, override_name: str
) -> None:
    target = flight("101", 9)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee("E001"),),
        employee_shifts=(shift("E001", role=role),),
        flights=(target,),
    )
    enabled_config = replace(OptimizerConfig(), **{config_field: True})

    assert build_candidate_assignments(day, OptimizerConfig()) == ()
    assert build_candidate_assignments(day, enabled_config) == (
        CandidateAssignment("E001", target),
    )
    assert build_candidate_assignments(
        day, enabled_config, **{override_name: False}
    ) == ()


def test_lead_emergency_config_does_not_enable_ordinary_candidates() -> None:
    target = flight("101", 9)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee("L001"),),
        employee_shifts=(shift("L001", role=OperationalRole.RAMP_LEAD),),
        flights=(target,),
    )
    config = replace(OptimizerConfig(), allow_leads_for_minimum_staffing=True)

    assert build_candidate_assignments(day, config) == ()


def test_empty_employees_and_flights_return_empty_tuple() -> None:
    day = OperationalDay(date(2026, 9, 2))

    assert build_candidate_assignments(day, OptimizerConfig()) == ()


def test_invalid_day_or_config_raises_aggregate_validation_error() -> None:
    day = OperationalDay(date(2026, 9, 2), flights=(Flight(),))

    with pytest.raises(InputValidationError):
        build_candidate_assignments(day, OptimizerConfig())
    with pytest.raises(InputValidationError):
        build_candidate_assignments(
            OperationalDay(date(2026, 9, 2)),
            replace(OptimizerConfig(), departure_work_minutes=0),
        )


def test_candidate_building_does_not_mutate_inputs() -> None:
    target = flight("101", 9)
    fixed = FixedAssignment("E001", target)
    day = OperationalDay(
        date(2026, 9, 2),
        employees=(employee("E001"),),
        employee_shifts=(shift("E001"),),
        flights=(target,),
        fixed_assignments=(fixed,),
    )
    config = OptimizerConfig()
    original_day = day
    original_config = config

    build_candidate_assignments(day, config)

    assert day == original_day
    assert config == original_config
    assert day.fixed_assignments == (fixed,)


def test_candidate_preprocessing_does_not_construct_solver_model(monkeypatch) -> None:
    day = OperationalDay(date(2026, 9, 2))

    def fail_if_constructed():
        raise AssertionError("candidate preprocessing must not construct CP-SAT")

    monkeypatch.setattr(cp_model, "CpModel", fail_if_constructed)

    assert build_candidate_assignments(day, OptimizerConfig()) == ()
