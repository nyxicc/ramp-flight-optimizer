"""Tests for aggregate operational-day validation."""

from datetime import date, datetime, timezone

import pytest

from ramp_optimizer import (
    Employee,
    EmployeeShift,
    Flight,
    InputValidationError,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    Qualification,
    validate_operational_day,
    validate_or_raise,
)


def employee(employee_id: str = "E001") -> Employee:
    return Employee(
        employee_id=employee_id,
        name="Avery Stone",
        qualifications=frozenset({Qualification.PUSH}),
    )


def shift(
    employee_id: str = "E001",
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 13),
) -> EmployeeShift:
    return EmployeeShift(
        employee_id=employee_id,
        start=start,
        end=end,
        normalized_role=OperationalRole.RAMP_AGENT,
    )


def arrival(number: object = "UA123") -> Flight:
    return Flight(
        arrival_flight_number=number,  # type: ignore[arg-type]
        arrival_time=datetime(2026, 9, 2, 8),
    )


def departure(number: object = "UA123") -> Flight:
    return Flight(
        departure_flight_number=number,  # type: ignore[arg-type]
        departure_time=datetime(2026, 9, 2, 10),
    )


def test_valid_day_passes_validation() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift(),),
        flights=(departure(),),
    )

    assert validate_operational_day(day) == ()
    validate_or_raise(day, OptimizerConfig())


def test_duplicate_arrival_numbers_are_case_insensitive() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(arrival("UA123"), arrival(" ua123 ")),
    )

    duplicate = next(
        issue
        for issue in validate_operational_day(day)
        if issue.code == "DUPLICATE_ARRIVAL_FLIGHT_NUMBER"
    )
    assert duplicate.path == "flights[1].arrival_flight_number"
    assert "flights[0]" in duplicate.message


def test_duplicate_departure_numbers_are_rejected() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(departure("1814"), departure("1814")),
    )

    assert any(
        issue.code == "DUPLICATE_DEPARTURE_FLIGHT_NUMBER"
        for issue in validate_operational_day(day)
    )


def test_same_number_once_in_each_direction_is_valid() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(arrival("1814"), departure("1814")),
    )

    assert validate_operational_day(day) == ()


def test_two_distinct_turn_pairs_are_valid() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(
            Flight(
                arrival_flight_number="1428",
                arrival_time=datetime(2026, 9, 2, 8),
                departure_flight_number="1814",
                departure_time=datetime(2026, 9, 2, 9),
            ),
            Flight(
                arrival_flight_number="1814",
                arrival_time=datetime(2026, 9, 2, 10),
                departure_flight_number="241",
                departure_time=datetime(2026, 9, 2, 11),
            ),
        ),
    )

    assert validate_operational_day(day) == ()


def test_duplicate_employee_ids_are_rejected() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee("E001"), employee(" e001 ")),
    )

    assert any(
        issue.code == "DUPLICATE_EMPLOYEE_ID"
        for issue in validate_operational_day(day)
    )


def test_invalid_and_overlapping_shifts_are_reported() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(
            shift(end=datetime(2026, 9, 2, 9)),
            shift(start=datetime(2026, 9, 2, 8), end=datetime(2026, 9, 2, 12)),
            shift(start=datetime(2026, 9, 2, 14), end=datetime(2026, 9, 2, 13)),
        ),
    )

    codes = {issue.code for issue in validate_operational_day(day)}

    assert "OVERLAPPING_EMPLOYEE_SHIFTS" in codes
    assert "INVALID_EMPLOYEE_SHIFT" in codes


def test_shift_must_reference_an_employee() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift("E999"),),
    )

    assert any(
        issue.code == "UNKNOWN_SHIFT_EMPLOYEE"
        for issue in validate_operational_day(day)
    )


def test_invalid_turn_order_is_reported() -> None:
    bad_turn = Flight(
        arrival_flight_number="UA456",
        arrival_time=datetime(2026, 9, 2, 11),
        departure_flight_number="UA789",
        departure_time=datetime(2026, 9, 2, 10),
    )

    codes = {
        issue.code
        for issue in validate_operational_day(
            OperationalDay(date(2026, 9, 2), flights=(bad_turn,))
        )
    }

    assert "INVALID_TURN_TIMES" in codes
    assert "INVALID_DERIVED_WORK_WINDOW" not in codes


def test_flight_requires_at_least_one_complete_side() -> None:
    day = OperationalDay(date(2026, 9, 2), flights=(Flight(),))

    assert {issue.code for issue in validate_operational_day(day)} == {
        "MISSING_FLIGHT_SIDES"
    }


@pytest.mark.parametrize(
    ("flight", "expected_code"),
    [
        (
            Flight(arrival_time=datetime(2026, 9, 2, 8)),
            "ARRIVAL_TIME_WITHOUT_FLIGHT_NUMBER",
        ),
        (Flight(arrival_flight_number="123"), "ARRIVAL_FLIGHT_NUMBER_WITHOUT_TIME"),
        (
            Flight(departure_time=datetime(2026, 9, 2, 8)),
            "DEPARTURE_TIME_WITHOUT_FLIGHT_NUMBER",
        ),
        (
            Flight(departure_flight_number="123"),
            "DEPARTURE_FLIGHT_NUMBER_WITHOUT_TIME",
        ),
    ],
)
def test_directional_number_and_timestamp_mismatches_are_specific(
    flight: Flight, expected_code: str
) -> None:
    codes = {
        issue.code
        for issue in validate_operational_day(
            OperationalDay(date(2026, 9, 2), flights=(flight,))
        )
    }

    assert expected_code in codes
    assert "INVALID_DERIVED_WORK_WINDOW" not in codes


@pytest.mark.parametrize(
    ("flight", "expected_code"),
    [
        (
            Flight(
                arrival_flight_number=" ",
                arrival_time=datetime(2026, 9, 2, 8),
            ),
            "BLANK_ARRIVAL_FLIGHT_NUMBER",
        ),
        (
            Flight(
                departure_flight_number="UA12A",
                departure_time=datetime(2026, 9, 2, 8),
            ),
            "MALFORMED_DEPARTURE_FLIGHT_NUMBER",
        ),
        (arrival(123), "INVALID_ARRIVAL_FLIGHT_NUMBER"),
    ],
)
def test_invalid_directional_numbers_are_reported_without_cascading(
    flight: Flight, expected_code: str
) -> None:
    codes = {
        issue.code
        for issue in validate_operational_day(
            OperationalDay(date(2026, 9, 2), flights=(flight,))
        )
    }

    assert expected_code in codes
    assert "INVALID_DERIVED_WORK_WINDOW" not in codes


def test_invalid_directional_datetime_is_reported_without_arithmetic() -> None:
    flight = Flight(
        arrival_flight_number="123",
        arrival_time="08:00",  # type: ignore[arg-type]
    )

    codes = {
        issue.code
        for issue in validate_operational_day(
            OperationalDay(date(2026, 9, 2), flights=(flight,))
        )
    }

    assert codes == {"INVALID_ARRIVAL_TIME"}


def test_mixed_mainline_and_express_turn_is_rejected() -> None:
    turn = Flight(
        arrival_flight_number="2999",
        arrival_time=datetime(2026, 9, 2, 8),
        departure_flight_number="3000",
        departure_time=datetime(2026, 9, 2, 9),
    )

    assert any(
        issue.code == "MIXED_TURN_SERVICE_CATEGORY"
        for issue in validate_operational_day(
            OperationalDay(date(2026, 9, 2), flights=(turn,))
        )
    )


def test_empty_flight_schedule_is_valid() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift(),),
    )

    assert validate_operational_day(day) == ()


def test_mixed_naive_and_aware_datetimes_are_rejected_without_arithmetic() -> None:
    turn = Flight(
        arrival_flight_number="UA123",
        arrival_time=datetime(2026, 9, 2, 8),
        departure_flight_number="UA456",
        departure_time=datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
    )
    day = OperationalDay(date(2026, 9, 2), flights=(turn,))

    codes = {issue.code for issue in validate_operational_day(day)}

    assert "MIXED_DATETIME_AWARENESS" in codes
    assert "INVALID_DERIVED_WORK_WINDOW" not in codes


def test_invalid_gate_and_heavy_types_are_reported() -> None:
    flight = Flight(
        arrival_flight_number="123",
        arrival_time=datetime(2026, 9, 2, 8),
        gate=4,  # type: ignore[arg-type]
        heavy=1,  # type: ignore[arg-type]
    )

    issues = validate_operational_day(
        OperationalDay(date(2026, 9, 2), flights=(flight,))
    )

    assert any(issue.code == "INVALID_GATE" for issue in issues)
    assert any(
        issue.code == "INVALID_BOOLEAN" and issue.path == "flights[0].heavy"
        for issue in issues
    )


def test_validate_or_raise_exposes_independent_issues() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(
            Flight(
                arrival_flight_number="FLIGHT",
                arrival_time=datetime(2026, 9, 2, 8),
                gate=4,  # type: ignore[arg-type]
            ),
        ),
    )

    with pytest.raises(InputValidationError) as error:
        validate_or_raise(day, OptimizerConfig())

    codes = {issue.code for issue in error.value.issues}
    assert codes == {"MALFORMED_ARRIVAL_FLIGHT_NUMBER", "INVALID_GATE"}
    assert "flights[0]" in str(error.value)


def test_malformed_text_fields_return_issues_instead_of_crashing() -> None:
    malformed_employee = Employee(
        employee_id=123,  # type: ignore[arg-type]
        name=None,  # type: ignore[arg-type]
    )
    day = OperationalDay(date(2026, 9, 2), employees=(malformed_employee,))

    codes = {issue.code for issue in validate_operational_day(day)}

    assert "INVALID_EMPLOYEE_ID" in codes
    assert "INVALID_EMPLOYEE_NAME" in codes
