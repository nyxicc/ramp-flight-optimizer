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


def test_valid_day_passes_validation() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift(),),
        flights=(Flight("UA123", departure_time=datetime(2026, 9, 2, 9)),),
    )

    assert validate_operational_day(day) == ()
    validate_or_raise(day, OptimizerConfig())


def test_duplicate_flight_numbers_are_case_insensitive() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        flights=(
            Flight("UA123", arrival_time=datetime(2026, 9, 2, 8)),
            Flight("ua123", departure_time=datetime(2026, 9, 2, 10)),
        ),
    )

    issues = validate_operational_day(day)

    duplicate = next(
        issue for issue in issues if issue.code == "DUPLICATE_FLIGHT_NUMBER"
    )
    assert duplicate.path == "flights[1].flight_number"
    assert "flights[0]" in duplicate.message


def test_duplicate_employee_ids_are_rejected() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee("E001"), employee(" e001 ")),
    )

    issues = validate_operational_day(day)

    assert any(issue.code == "DUPLICATE_EMPLOYEE_ID" for issue in issues)


def test_invalid_and_overlapping_shifts_are_reported() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(
            shift(end=datetime(2026, 9, 2, 9)),
            shift(
                start=datetime(2026, 9, 2, 8),
                end=datetime(2026, 9, 2, 12),
            ),
            shift(
                start=datetime(2026, 9, 2, 14),
                end=datetime(2026, 9, 2, 13),
            ),
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


def test_invalid_turn_is_reported() -> None:
    bad_turn = Flight(
        "UA456",
        arrival_time=datetime(2026, 9, 2, 11),
        departure_time=datetime(2026, 9, 2, 10),
    )
    day = OperationalDay(
        operational_date=date(2026, 9, 2), flights=(bad_turn,)
    )

    assert any(
        issue.code == "INVALID_TURN_TIMES"
        for issue in validate_operational_day(day)
    )


def test_flight_requires_at_least_one_timestamp() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2), flights=(Flight("UA789"),)
    )

    assert any(
        issue.code == "MISSING_FLIGHT_TIMES"
        for issue in validate_operational_day(day)
    )


def test_empty_flight_schedule_is_valid() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift(),),
    )

    assert validate_operational_day(day) == ()


def test_mixed_naive_and_aware_datetimes_are_rejected() -> None:
    aware_departure = datetime(2026, 9, 2, 10, tzinfo=timezone.utc)
    day = OperationalDay(
        operational_date=date(2026, 9, 2),
        employees=(employee(),),
        employee_shifts=(shift(),),
        flights=(Flight("UA123", departure_time=aware_departure),),
    )

    assert any(
        issue.code == "MIXED_DATETIME_AWARENESS"
        for issue in validate_operational_day(day)
    )


def test_validate_or_raise_exposes_all_issues() -> None:
    day = OperationalDay(
        operational_date=date(2026, 9, 2), flights=(Flight(""),)
    )

    with pytest.raises(InputValidationError) as error:
        validate_or_raise(day, OptimizerConfig())

    codes = {issue.code for issue in error.value.issues}
    assert codes == {"INVALID_FLIGHT_NUMBER", "MISSING_FLIGHT_TIMES"}
    assert "flights[0]" in str(error.value)


def test_malformed_text_fields_return_issues_instead_of_crashing() -> None:
    malformed_employee = Employee(
        employee_id=123,  # type: ignore[arg-type]
        name=None,  # type: ignore[arg-type]
    )
    day = OperationalDay(
        operational_date=date(2026, 9, 2), employees=(malformed_employee,)
    )

    codes = {issue.code for issue in validate_operational_day(day)}

    assert "INVALID_EMPLOYEE_ID" in codes
    assert "INVALID_EMPLOYEE_NAME" in codes
