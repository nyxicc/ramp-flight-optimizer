"""Tests for immutable input and result vocabulary."""

from dataclasses import FrozenInstanceError, fields
from datetime import datetime

import pytest

from ramp_optimizer import (
    BreakStatus,
    CandidateAssignment,
    EligibilityAssessment,
    EligibilityReason,
    Employee,
    EmployeeShift,
    FairnessMetrics,
    FixedAssignment,
    Flight,
    OperationalDay,
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


def test_fixed_and_candidate_models_retain_flight_without_synthetic_id() -> None:
    flight = Flight(
        departure_flight_number="2690",
        departure_time=datetime(2026, 9, 2, 6),
    )
    fixed = FixedAssignment("E001", flight)
    candidate = CandidateAssignment("E001", flight)
    day = OperationalDay(
        operational_date=datetime(2026, 9, 2).date(),
        flights=(flight,),
        fixed_assignments=(fixed,),
    )

    assert fixed.flight is flight
    assert candidate.flight is flight
    assert day.fixed_assignments == (fixed,)
    assert not hasattr(fixed, "flight_id")
    with pytest.raises(FrozenInstanceError):
        fixed.employee_id = "E002"  # type: ignore[misc]


def test_eligibility_assessment_enforces_reason_invariant() -> None:
    flight = Flight(
        departure_flight_number="2690",
        departure_time=datetime(2026, 9, 2, 6),
    )

    eligible = EligibilityAssessment("E001", flight, True, ())
    ineligible = EligibilityAssessment(
        "E001", flight, False, (EligibilityReason.OUTSIDE_SHIFT,)
    )

    assert eligible.reasons == ()
    assert not ineligible.eligible
    with pytest.raises(ValueError):
        EligibilityAssessment(
            "E001", flight, True, (EligibilityReason.OUTSIDE_SHIFT,)
        )
    with pytest.raises(ValueError):
        EligibilityAssessment("E001", flight, False, ())


def test_break_status_vocabulary_matches_approved_reporting() -> None:
    assert BreakStatus.NOT_APPLICABLE.value == "NOT_APPLICABLE"
    assert (
        BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS.value
        == "NOT_EVALUABLE_BETWEEN_ASSIGNMENTS"
    )
    assert BreakStatus.SATISFIED.value == "SATISFIED"
    assert BreakStatus.UNSATISFIED.value == "UNSATISFIED"


def test_future_fairness_metrics_can_remain_explicitly_unevaluated() -> None:
    metrics = FairnessMetrics(
        participating_employee_count=2,
        total_assignments=3,
        average_flights=1.5,
        highest_flight_count=2,
        lowest_flight_count=1,
        flight_count_spread=1,
        maximum_consecutive_streak=None,
        adjusted_workload_spread=None,
    )

    assert metrics.maximum_consecutive_streak is None
    assert metrics.adjusted_workload_spread is None
