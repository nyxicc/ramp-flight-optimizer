"""Tests for fixed-assignment structural and legality validation."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from ramp_optimizer import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    validate_operational_day,
)


def at(
    hour: int, minute: int = 0, *, aware: bool = False
) -> datetime:
    return datetime(
        2026,
        9,
        2,
        hour,
        minute,
        tzinfo=timezone.utc if aware else None,
    )


def flight(number: str, hour: int, minute: int = 0, *, aware: bool = False) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute, aware=aware),
    )


def employee(*, enabled: bool = True) -> Employee:
    return Employee("E001", "Avery Stone", enabled=enabled)


def employee_shift(
    *,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 13),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift("E001", start, end, role)


def day_with_fixed(
    flights: tuple[Flight, ...],
    fixed: tuple[FixedAssignment, ...],
    *,
    roster_employee: Employee | None = None,
    shifts: tuple[EmployeeShift, ...] | None = None,
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=(roster_employee or employee(),),
        employee_shifts=shifts if shifts is not None else (employee_shift(),),
        flights=flights,
        fixed_assignments=fixed,
    )


def test_unknown_fixed_employee_reference_is_structured() -> None:
    target = flight("101", 9)
    day = day_with_fixed(
        (target,), (FixedAssignment("E999", target),)
    )

    issues = validate_operational_day(day)

    assert any(
        issue.code == "UNKNOWN_FIXED_ASSIGNMENT_EMPLOYEE" for issue in issues
    )


def test_unknown_fixed_flight_reference_is_structured() -> None:
    scheduled = flight("101", 9)
    unscheduled = flight("102", 10)
    day = day_with_fixed(
        (scheduled,), (FixedAssignment("E001", unscheduled),)
    )

    issues = validate_operational_day(day)

    assert any(issue.code == "UNKNOWN_FIXED_ASSIGNMENT_FLIGHT" for issue in issues)


def test_duplicate_fixed_assignment_is_rejected_at_later_path() -> None:
    target = flight("101", 9)
    fixed = FixedAssignment("E001", target)
    day = day_with_fixed((target,), (fixed, fixed))

    duplicate = next(
        issue
        for issue in validate_operational_day(day)
        if issue.code == "DUPLICATE_FIXED_ASSIGNMENT"
    )

    assert duplicate.path == "fixed_assignments[1]"
    assert "fixed_assignments[0]" in duplicate.message


def test_overlapping_fixed_assignments_for_employee_are_rejected() -> None:
    first = flight("101", 9)
    second = flight("102", 9, 30)
    day = day_with_fixed(
        (first, second),
        (FixedAssignment("E001", first), FixedAssignment("E001", second)),
    )

    issues = validate_operational_day(day)

    assert any(issue.code == "OVERLAPPING_FIXED_ASSIGNMENTS" for issue in issues)


def test_endpoint_touching_fixed_assignments_are_valid() -> None:
    first = flight("101", 9)
    second = flight("102", 10)
    day = day_with_fixed(
        (first, second),
        (FixedAssignment("E001", first), FixedAssignment("E001", second)),
    )

    assert validate_operational_day(day) == ()


@pytest.mark.parametrize(
    ("roster_employee", "shifts"),
    [
        (employee(enabled=False), (employee_shift(),)),
        (
            employee(),
            (employee_shift(role=OperationalRole.RAMP_LEAD),),
        ),
        (
            employee(),
            (employee_shift(start=at(5), end=at(7)),),
        ),
    ],
)
def test_disabled_role_ineligible_or_outside_fixed_assignment_is_illegal(
    roster_employee: Employee, shifts: tuple[EmployeeShift, ...]
) -> None:
    target = flight("101", 9)
    day = day_with_fixed(
        (target,),
        (FixedAssignment("E001", target),),
        roster_employee=roster_employee,
        shifts=shifts,
    )

    issues = validate_operational_day(day)

    assert any(issue.code == "ILLEGAL_FIXED_ASSIGNMENT" for issue in issues)


def test_explicit_lead_policy_can_make_fixed_assignment_legal() -> None:
    target = flight("101", 9)
    day = day_with_fixed(
        (target,),
        (FixedAssignment("E001", target),),
        shifts=(employee_shift(role=OperationalRole.RAMP_LEAD),),
    )

    assert any(
        issue.code == "ILLEGAL_FIXED_ASSIGNMENT"
        for issue in validate_operational_day(day)
    )
    assert validate_operational_day(day, include_leads=True) == ()


def test_unqualified_employee_may_hold_legal_fixed_assignment() -> None:
    target = flight("101", 9)
    equal_reference = replace(target)
    day = day_with_fixed(
        (target,), (FixedAssignment("E001", equal_reference),)
    )

    assert target is not equal_reference
    assert validate_operational_day(day, OptimizerConfig()) == ()


def test_invalid_referenced_flight_does_not_cascade_into_fixed_arithmetic() -> None:
    malformed = flight("UA", 9)
    day = day_with_fixed(
        (malformed,), (FixedAssignment("E001", malformed),)
    )

    codes = {issue.code for issue in validate_operational_day(day)}

    assert codes == {"MALFORMED_DEPARTURE_FLIGHT_NUMBER"}


def test_mixed_timezone_awareness_does_not_crash_fixed_conflict_validation() -> None:
    naive = flight("101", 9)
    aware = flight("102", 10, aware=True)
    day = day_with_fixed(
        (naive, aware),
        (FixedAssignment("E001", naive), FixedAssignment("E001", aware)),
    )

    codes = {issue.code for issue in validate_operational_day(day)}

    assert "MIXED_DATETIME_AWARENESS" in codes
    assert "OVERLAPPING_FIXED_ASSIGNMENTS" not in codes
