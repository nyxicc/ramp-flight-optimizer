"""Pure deterministic supervisor-facing optimization reporting."""

from ramp_optimizer.models import OptimizationResult


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def format_optimization_report(result: OptimizationResult) -> str:
    """Return a concise plain-text report without printing or file access."""

    summary = result.schedule_summary
    all_optimal = (
        summary.all_objectives_proven_optimal
        if summary is not None
        else bool(result.objective_values)
        and all(item.proven_optimal for item in result.objective_values)
    )
    lines = [
        "Ramp Team Flight Optimizer",
        f"Readiness: {result.operational_readiness.value}",
        (
            f"Solver: {result.status.value}; all objectives proven optimal: "
            f"{_yes_no(all_optimal)}"
        ),
        (
            f"Emergency recovery: enabled={_yes_no(result.emergency_leads_enabled)}; "
            f"disposition={result.emergency_pass_disposition.value}; "
            f"Lead assignments={len(result.lead_assignments)}"
        ),
    ]

    if summary is None:
        lines.append("Schedule summary: unavailable")
    else:
        lines.extend(
            (
                (
                    f"Flights: {summary.total_flights}; minimum staffed "
                    f"{summary.minimum_staffed_flights}/{summary.total_flights}; "
                    f"below minimum {summary.below_minimum_flights}; preferred "
                    f"staffed {summary.preferred_staffed_flights}"
                ),
                (
                    f"Qualifications: compliant "
                    f"{summary.qualification_compliant_flights}/"
                    f"{summary.qualification_required_flights}; missing push "
                    f"{summary.missing_push_flights}; missing close-out "
                    f"{summary.missing_close_out_flights}"
                ),
                (
                    f"Breaks: satisfied {summary.employees_with_satisfied_break}; "
                    f"unsatisfied {summary.employees_with_unsatisfied_break}; "
                    f"not evaluable {summary.employees_with_nonevaluable_break}"
                ),
                (
                    f"Assignments: {summary.total_assignments}; participating "
                    f"employees {summary.participating_employee_count}"
                ),
            )
        )

    lines.append("Lead interventions:")
    if result.lead_assignments:
        lines.extend(
            f"- {assignment.message}" for assignment in result.lead_assignments
        )
    else:
        lines.append("- None")

    lines.append("Warnings:")
    if result.warnings:
        lines.extend(
            f"- [{warning.severity.value}] {warning.code.value}: {warning.message}"
            for warning in result.warnings
        )
    else:
        lines.append("- None")

    fairness = result.fairness_metrics
    continuity = result.continuity_metrics
    if fairness is not None:
        lines.append(
            "Fairness: "
            f"flight-count spread {fairness.flight_count_spread}; "
            f"maximum streak {fairness.maximum_consecutive_streak}; "
            f"adjusted-workload spread {fairness.adjusted_workload_spread}"
        )
    else:
        lines.append("Fairness: unavailable")
    if continuity is not None:
        lines.append(
            "Continuity: "
            f"{continuity.total_retained_employee_transitions} retained employee "
            f"transitions across {continuity.eligible_transition_count} eligible pairs"
        )
    else:
        lines.append("Continuity: unavailable")

    lines.append(
        f"Runtime: {result.solver_runtime_seconds:.3f}s overall; "
        f"{len(result.attempts)} attempt(s)"
    )
    for attempt in result.attempts:
        lines.append(
            f"- Pass {attempt.pass_number} {attempt.attempt_label}: "
            f"status={attempt.status.value}; "
            f"usable={_yes_no(attempt.usable_schedule)}; "
            f"selected={_yes_no(attempt.selected_as_final)}; "
            f"minimum={attempt.minimum_staffed_flights}; "
            f"qualification-compliant={attempt.qualification_compliant_flights}; "
            f"critical shortages={attempt.critical_shortage_count}; "
            f"unsatisfied breaks={attempt.known_unsatisfied_required_break_count}; "
            f"Lead candidates={attempt.lead_candidate_count}; "
            f"Lead assignments={attempt.lead_assignments}; "
            f"stages={attempt.objective_stages_completed}; "
            f"all optimal={_yes_no(attempt.all_objectives_proven_optimal)}; "
            f"runtime={attempt.solver_runtime_seconds:.3f}s"
        )
    return "\n".join(lines)
