"""Pure assignment-eligibility checks over discrete employee shifts."""

from datetime import datetime
from typing import Iterable

from ramp_optimizer.enums import OperationalRole
from ramp_optimizer.models import Employee, EmployeeShift


def role_is_assignment_eligible(
    role: OperationalRole,
    *,
    include_leads: bool = False,
    allow_trainees: bool = False,
    allow_possible_ramp_support: bool = False,
) -> bool:
    """Return whether a normalized role may perform ordinary ramp assignments."""

    if role is OperationalRole.RAMP_AGENT:
        return True
    if role is OperationalRole.RAMP_LEAD:
        return include_leads
    if role is OperationalRole.TRAINEE:
        return allow_trainees
    if role is OperationalRole.POSSIBLE_RAMP_SUPPORT:
        return allow_possible_ramp_support
    return False


def eligible_shifts_for_interval(
    employee: Employee,
    shifts: Iterable[EmployeeShift],
    work_start: datetime,
    work_end: datetime,
    *,
    include_leads: bool = False,
    allow_trainees: bool = False,
    allow_possible_ramp_support: bool = False,
) -> tuple[EmployeeShift, ...]:
    """Return shifts that individually contain the complete half-open work interval.

    Separate shifts are never merged, so a work interval spanning a gap between two
    records is unavailable even when its endpoints fall within their combined span.
    """

    if not employee.enabled or work_start >= work_end:
        return ()

    eligible: list[EmployeeShift] = []
    normalized_employee_id = employee.employee_id.strip().casefold()
    for shift in shifts:
        if shift.employee_id.strip().casefold() != normalized_employee_id:
            continue
        if not role_is_assignment_eligible(
            shift.normalized_role,
            include_leads=include_leads,
            allow_trainees=allow_trainees,
            allow_possible_ramp_support=allow_possible_ramp_support,
        ):
            continue
        try:
            contains_interval = (
                shift.start <= work_start and work_end <= shift.end
            )
        except TypeError:
            contains_interval = False
        if contains_interval:
            eligible.append(shift)
    return tuple(eligible)


def employee_is_available_for_interval(
    employee: Employee,
    shifts: Iterable[EmployeeShift],
    work_start: datetime,
    work_end: datetime,
    *,
    include_leads: bool = False,
    allow_trainees: bool = False,
    allow_possible_ramp_support: bool = False,
) -> bool:
    """Return whether at least one eligible shift contains the entire interval."""

    return bool(
        eligible_shifts_for_interval(
            employee,
            shifts,
            work_start,
            work_end,
            include_leads=include_leads,
            allow_trainees=allow_trainees,
            allow_possible_ramp_support=allow_possible_ramp_support,
        )
    )
