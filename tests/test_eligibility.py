"""Tests for assignment legality across separate availability intervals."""

from datetime import datetime

from ramp_optimizer import (
    Employee,
    EmployeeShift,
    OperationalRole,
    Qualification,
    employee_is_available_for_interval,
)

DAY = (2026, 9, 2)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(*DAY, hour, minute)


def employee(enabled: bool = True) -> Employee:
    return Employee(
        "E001",
        "Avery Stone",
        frozenset({Qualification.PUSH}),
        enabled,
    )


def shift(start: datetime, end: datetime, role: OperationalRole) -> EmployeeShift:
    return EmployeeShift("E001", start, end, role)


def test_complete_interval_must_fit_inside_one_shift() -> None:
    shifts = (
        shift(at(5), at(9), OperationalRole.RAMP_AGENT),
        shift(at(10), at(14), OperationalRole.RAMP_AGENT),
    )

    assert employee_is_available_for_interval(employee(), shifts, at(6), at(8))
    assert not employee_is_available_for_interval(employee(), shifts, at(8), at(11))


def test_interval_ending_exactly_at_shift_end_is_available() -> None:
    shifts = (shift(at(5), at(9), OperationalRole.RAMP_AGENT),)

    assert employee_is_available_for_interval(employee(), shifts, at(8), at(9))


def test_overnight_shift_uses_full_datetimes() -> None:
    overnight = EmployeeShift(
        "E001",
        at(22),
        datetime(2026, 9, 3, 6),
        OperationalRole.RAMP_AGENT,
    )

    assert employee_is_available_for_interval(
        employee(),
        (overnight,),
        datetime(2026, 9, 3, 1),
        datetime(2026, 9, 3, 2),
    )


def test_ineligible_roles_disabled_employees_and_unknown_records_are_excluded() -> None:
    interval = (at(6), at(7))

    for role in (
        OperationalRole.TRAINEE,
        OperationalRole.NON_RAMP,
        OperationalRole.UNKNOWN,
        OperationalRole.POSSIBLE_RAMP_SUPPORT,
        OperationalRole.RAMP_LEAD,
    ):
        assert not employee_is_available_for_interval(
            employee(), (shift(at(5), at(9), role),), *interval
        )
    assert not employee_is_available_for_interval(
        employee(enabled=False),
        (shift(at(5), at(9), OperationalRole.RAMP_AGENT),),
        *interval,
    )


def test_lead_and_training_eligibility_require_explicit_overrides() -> None:
    interval = (at(6), at(7))
    lead = shift(at(5), at(9), OperationalRole.RAMP_LEAD)
    trainee = shift(at(5), at(9), OperationalRole.TRAINEE)

    assert employee_is_available_for_interval(
        employee(), (lead,), *interval, include_leads=True
    )
    assert employee_is_available_for_interval(
        employee(), (trainee,), *interval, allow_trainees=True
    )


def test_position_never_grants_a_qualification() -> None:
    unqualified = Employee("E001", "Avery Stone")
    ramp_shift = shift(at(5), at(9), OperationalRole.RAMP_AGENT)

    assert employee_is_available_for_interval(
        unqualified, (ramp_shift,), at(6), at(7)
    )
    assert unqualified.qualifications == frozenset()
