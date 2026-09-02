"""Validated candidate preprocessing for a future scheduling solver."""

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.eligibility import assess_employee_flight_eligibility
from ramp_optimizer.models import CandidateAssignment, OperationalDay
from ramp_optimizer.validation import validate_or_raise


def build_candidate_assignments(
    day: OperationalDay,
    config: OptimizerConfig,
    *,
    include_leads: bool = False,
    allow_trainees: bool = False,
    allow_possible_ramp_support: bool = False,
) -> tuple[CandidateAssignment, ...]:
    """Return legal, non-fixed pairs in employee order then flight order."""

    validate_or_raise(
        day,
        config,
        include_leads=include_leads,
        allow_trainees=allow_trainees,
        allow_possible_ramp_support=allow_possible_ramp_support,
    )
    candidates: list[CandidateAssignment] = []
    for employee in day.employees:
        for flight in day.flights:
            assessment = assess_employee_flight_eligibility(
                employee,
                day.employee_shifts,
                flight,
                config,
                day.fixed_assignments,
                include_leads=include_leads,
                allow_trainees=allow_trainees,
                allow_possible_ramp_support=allow_possible_ramp_support,
            )
            if assessment.eligible and not assessment.fixed_to_target:
                candidates.append(
                    CandidateAssignment(employee_id=employee.employee_id, flight=flight)
                )
    return tuple(candidates)
