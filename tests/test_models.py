"""Tests for immutable input and result vocabulary."""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from ramp_optimizer import (
    BreakStatus,
    Employee,
    EmployeeShift,
    Flight,
    OperationalRole,
    Qualification,
)


def test_input_models_are_immutable() -> None:
    employee = Employee(
        employee_id="E001",
        name="Avery Stone",
        qualifications=frozenset({Qualification.PUSH}),
    )

    with pytest.raises(FrozenInstanceError):
        employee.name = "Changed"  # type: ignore[misc]


def test_employee_identity_is_separate_from_daily_shifts() -> None:
    employee = Employee(
        employee_id="E001",
        name="Avery Stone",
        qualifications=frozenset({Qualification.PUSH, Qualification.CLOSE_OUT}),
    )
    shift = EmployeeShift(
        employee_id=employee.employee_id,
        start=datetime(2026, 9, 2, 5),
        end=datetime(2026, 9, 2, 9),
        source_position="Ramp Agent",
        normalized_role=OperationalRole.RAMP_AGENT,
    )

    assert not hasattr(employee, "position")
    assert not hasattr(employee, "shift_start")
    assert Qualification.PUSH in employee.qualifications
    assert shift.employee_id == employee.employee_id


def test_flight_stores_source_times_without_deriving_phase_two_values() -> None:
    arrival = datetime(2026, 9, 2, 8)
    flight = Flight(flight_number="UA123", arrival_time=arrival)

    assert flight.arrival_time == arrival
    assert flight.departure_time is None
    assert not hasattr(flight, "work_start")


def test_break_status_vocabulary_matches_approved_reporting() -> None:
    assert BreakStatus.NOT_APPLICABLE.value == "NOT_APPLICABLE"
    assert (
        BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS.value
        == "NOT_EVALUABLE_BETWEEN_ASSIGNMENTS"
    )
    assert BreakStatus.SATISFIED.value == "SATISFIED"
    assert BreakStatus.UNSATISFIED.value == "UNSATISFIED"
