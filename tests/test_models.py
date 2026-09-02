"""Tests for immutable input and result vocabulary."""

from dataclasses import FrozenInstanceError, fields
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
        normalized_role=OperationalRole.RAMP_AGENT,
    )

    assert not hasattr(employee, "position")
    assert not hasattr(employee, "shift_start")
    assert Qualification.PUSH in employee.qualifications
    assert shift.employee_id == employee.employee_id
    assert not hasattr(shift, "source_row")
    assert not hasattr(shift, "source_position")
    assert not hasattr(shift, "notes_present")
    assert not hasattr(shift, "swapboard")


def test_arrival_only_flight_construction() -> None:
    arrival = datetime(2026, 9, 2, 9, 2)
    flight = Flight(arrival_flight_number="1428", arrival_time=arrival)

    assert flight.arrival_flight_number == "1428"
    assert flight.arrival_time == arrival
    assert flight.departure_flight_number is None
    assert flight.departure_time is None


def test_departure_only_flight_construction() -> None:
    departure = datetime(2026, 9, 2, 6)
    flight = Flight(departure_flight_number="2690", departure_time=departure)

    assert flight.arrival_flight_number is None
    assert flight.arrival_time is None
    assert flight.departure_flight_number == "2690"
    assert flight.departure_time == departure


def test_turn_retains_optional_gate_and_is_immutable() -> None:
    flight = Flight(
        arrival_flight_number="1428",
        arrival_time=datetime(2026, 9, 2, 9, 2),
        departure_flight_number="1814",
        departure_time=datetime(2026, 9, 2, 10, 10),
        gate="B4",
    )

    assert flight.gate == "B4"
    with pytest.raises(FrozenInstanceError):
        flight.gate = "B5"  # type: ignore[misc]


def test_flight_does_not_store_derived_values() -> None:
    field_names = {field.name for field in fields(Flight)}

    assert field_names == {
        "arrival_flight_number",
        "departure_flight_number",
        "arrival_time",
        "departure_time",
        "gate",
        "heavy",
    }


def test_break_status_vocabulary_matches_approved_reporting() -> None:
    assert BreakStatus.NOT_APPLICABLE.value == "NOT_APPLICABLE"
    assert (
        BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS.value
        == "NOT_EVALUABLE_BETWEEN_ASSIGNMENTS"
    )
    assert BreakStatus.SATISFIED.value == "SATISFIED"
    assert BreakStatus.UNSATISFIED.value == "UNSATISFIED"
