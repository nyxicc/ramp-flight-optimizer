"""Pure, explainable assignment eligibility over discrete employee shifts."""

from datetime import datetime
from typing import Iterable

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.enums import EligibilityReason, OperationalRole
from ramp_optimizer.intervals import intervals_overlap
from ramp_optimizer.models import (
    EligibilityAssessment,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
)
from ramp_optimizer.timing import derive_flight_operational_facts


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


def assess_employee_flight_eligibility(
    employee: Employee,
    shifts: Iterable[EmployeeShift],
    flight: Flight,
    config: OptimizerConfig,
    fixed_assignments: Iterable[FixedAssignment] = (),
    *,
    include_leads: bool = False,
    allow_trainees: bool = False,
    allow_possible_ramp_support: bool = False,
) -> EligibilityAssessment:
    """Return a deterministic legal assessment for one employee-flight pair.

    Reasons follow a concise precedence order: disabled status, missing shifts,
    role eligibility, full-window containment, then fixed-assignment conflicts.
    Qualifications are intentionally not part of ordinary assignment eligibility.
    """

    employee_shifts = tuple(
        shift
        for shift in shifts
        if _same_employee_id(shift.employee_id, employee.employee_id)
    )
    fixed_values = tuple(fixed_assignments)
    fixed_to_target = any(
        _same_employee_id(fixed.employee_id, employee.employee_id)
        and fixed.flight == flight
        for fixed in fixed_values
    )
    facts = derive_flight_operational_facts(flight, config)

    if not employee.enabled:
        return _ineligible(
            employee, flight, EligibilityReason.EMPLOYEE_DISABLED, fixed_to_target
        )
    if not employee_shifts:
        return _ineligible(
            employee, flight, EligibilityReason.NO_EMPLOYEE_SHIFT, fixed_to_target
        )

    role_eligible_shifts = tuple(
        shift
        for shift in employee_shifts
        if role_is_assignment_eligible(
            shift.normalized_role,
            include_leads=include_leads,
            allow_trainees=allow_trainees,
            allow_possible_ramp_support=allow_possible_ramp_support,
        )
    )
    if not role_eligible_shifts:
        return _ineligible(
            employee,
            flight,
            EligibilityReason.INELIGIBLE_OPERATIONAL_ROLE,
            fixed_to_target,
        )

    containing_shifts = eligible_shifts_for_interval(
        employee,
        role_eligible_shifts,
        facts.work_start,
        facts.work_end,
        include_leads=include_leads,
        allow_trainees=allow_trainees,
        allow_possible_ramp_support=allow_possible_ramp_support,
    )
    if not containing_shifts:
        return _ineligible(
            employee, flight, EligibilityReason.OUTSIDE_SHIFT, fixed_to_target
        )

    for fixed in fixed_values:
        if not _same_employee_id(fixed.employee_id, employee.employee_id):
            continue
        if fixed.flight == flight:
            continue
        fixed_facts = derive_flight_operational_facts(fixed.flight, config)
        if intervals_overlap(
            facts.work_start,
            facts.work_end,
            fixed_facts.work_start,
            fixed_facts.work_end,
        ):
            return _ineligible(
                employee,
                flight,
                EligibilityReason.OVERLAPS_FIXED_ASSIGNMENT,
                fixed_to_target,
            )

    return EligibilityAssessment(
        employee_id=employee.employee_id,
        flight=flight,
        eligible=True,
        reasons=(),
        fixed_to_target=fixed_to_target,
    )


def _ineligible(
    employee: Employee,
    flight: Flight,
    reason: EligibilityReason,
    fixed_to_target: bool,
) -> EligibilityAssessment:
    return EligibilityAssessment(
        employee_id=employee.employee_id,
        flight=flight,
        eligible=False,
        reasons=(reason,),
        fixed_to_target=fixed_to_target,
    )


def _same_employee_id(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()
