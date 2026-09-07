"""Lexicographic ramp optimizer with optional emergency-Lead recovery."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import combinations
from math import isfinite
from time import monotonic

from ortools.sat.python import cp_model

from ramp_optimizer.candidates import build_candidate_assignments
from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.eligibility import (
    eligible_shifts_for_interval,
    role_is_assignment_eligible,
)
from ramp_optimizer.intervals import intervals_overlap
from ramp_optimizer.models import (
    CandidateAssignment,
    ContinuityMetrics,
    ContinuityTransitionResult,
    Employee,
    EmployeeScheduleResult,
    EmergencyLeadAssignmentResult,
    FairnessMetrics,
    FlightAssignmentResult,
    ObjectiveValue,
    OperationalDay,
    OptimizationAttemptSummary,
    OptimizationResult,
    ScheduleSummary,
    ScheduleWarning,
)
from ramp_optimizer.enums import (
    BreakStatus,
    EmergencyLeadReason,
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    FlightType,
    OperationalRole,
    OptimizationStatus,
    OperationalReadinessStatus,
    Qualification,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
)
from ramp_optimizer.staffing import StaffingRequirements, staffing_requirements_for
from ramp_optimizer.timing import (
    FlightOperationalFacts,
    derive_flight_operational_facts,
)
from ramp_optimizer.workload import (
    adjusted_assignment_workload_units,
    scaled_workload_factors,
    workload_units_to_public_value,
)


@dataclass(slots=True)
class _ModelData:
    model: cp_model.CpModel
    decisions: dict[tuple[int, int], cp_model.IntVar]
    facts: tuple[FlightOperationalFacts, ...]
    requirements: tuple[StaffingRequirements, ...]
    fixed_employee_indices: tuple[tuple[int, ...], ...]
    staff_counts: tuple[cp_model.IntVar, ...]
    minimum_met: tuple[cp_model.IntVar, ...]
    minimum_shortfalls: tuple[cp_model.IntVar, ...]
    preferred_met: tuple[cp_model.IntVar, ...]
    preferred_shortfalls: tuple[cp_model.IntVar, ...]
    largest_minimum_shortfall: cp_model.IntVar
    push_covered: tuple[cp_model.IntVar | None, ...]
    close_covered: tuple[cp_model.IntVar | None, ...]
    qualification_compliant: tuple[cp_model.IntVar | None, ...]
    minimum_staffed_qualification_compliant: tuple[cp_model.IntVar, ...]
    minimum_staffed_qualification_coverage: tuple[cp_model.IntVar, ...]
    partial_crew_qualification_coverage: tuple[cp_model.IntVar, ...]
    included_employee_indices: tuple[int, ...]
    break_evaluable: tuple[cp_model.IntVar | None, ...]
    break_achieved: tuple[cp_model.IntVar | None, ...]
    known_unsatisfied_break: tuple[cp_model.IntVar | None, ...]
    break_gap_variables: dict[tuple[int, int, int], cp_model.IntVar]
    fairness_employee_indices: tuple[int, ...]
    fairness_flight_counts: tuple[cp_model.IntVar | None, ...]
    highest_flight_count: cp_model.IntVar
    lowest_flight_count: cp_model.IntVar
    flight_count_spread: cp_model.IntVar
    pairwise_flight_count_differences: tuple[cp_model.IntVar, ...]
    total_pairwise_flight_count_difference: cp_model.IntVar
    streak_predecessor_arcs: dict[tuple[int, int, int], cp_model.IntVar]
    streak_run_lengths: dict[tuple[int, int], cp_model.IntVar]
    employee_longest_streaks: tuple[cp_model.IntVar | None, ...]
    maximum_consecutive_flight_streak: cp_model.IntVar
    total_employee_longest_streaks: cp_model.IntVar
    fairness_shift_minutes: tuple[int | None, ...]
    total_fairness_shift_minutes: int
    total_fairness_assignment_count: cp_model.IntVar
    shift_adjusted_deviations: tuple[cp_model.IntVar | None, ...]
    total_shift_adjusted_deviation: cp_model.IntVar
    three_person_staffing: tuple[cp_model.IntVar, ...]
    assigned_to_three_person_flight: dict[tuple[int, int], cp_model.IntVar]
    adjusted_workload_units: tuple[cp_model.IntVar | None, ...]
    highest_adjusted_workload: cp_model.IntVar
    lowest_adjusted_workload: cp_model.IntVar
    adjusted_workload_spread: cp_model.IntVar
    pairwise_adjusted_workload_differences: tuple[cp_model.IntVar, ...]
    total_pairwise_adjusted_workload_difference: cp_model.IntVar
    continuity_flight_pairs: tuple[tuple[int, int], ...]
    retained_employee_transitions: dict[
        tuple[int, int, int], cp_model.IntVar
    ]
    total_continuity_retention: cp_model.IntVar
    include_leads: bool
    lead_decisions: dict[tuple[int, int], cp_model.IntVar]
    total_lead_assignments: cp_model.IntVar


@dataclass(frozen=True, slots=True)
class _CriticalFlightNeed:
    """Critical defects in one Pass-1 flight result."""

    flight_index: int
    below_minimum: bool
    missing_push: bool
    missing_close: bool


@dataclass(frozen=True, slots=True)
class _CriticalOperationalScore:
    """Reconstructed pre-Lead objectives in exact priority order."""

    minimum_staffed_flights: int
    qualification_compliant_flights: int
    individual_qualification_coverage: int
    total_minimum_shortfall: int
    largest_minimum_shortfall: int
    known_unsatisfied_breaks: int

    @property
    def comparison_key(self) -> tuple[int, ...]:
        return (
            self.minimum_staffed_flights,
            self.qualification_compliant_flights,
            self.individual_qualification_coverage,
            -self.total_minimum_shortfall,
            -self.largest_minimum_shortfall,
            -self.known_unsatisfied_breaks,
        )


@dataclass(frozen=True, slots=True)
class _ObjectiveStage:
    name: str
    maximize: bool
    expression: cp_model.LinearExpr | int


def optimize_flight_assignments(
    day: OperationalDay, config: OptimizerConfig | None = None
) -> OptimizationResult:
    """Run the ordinary schedule first, then emergency Lead recovery if needed."""

    active_config = config or OptimizerConfig()
    overall_started_at = monotonic()
    pass_one, _ = _run_optimization_pass(
        day,
        active_config,
        include_leads=False,
        critical_needs=(),
    )
    pass_one_usable = _result_has_usable_schedule(day, pass_one)
    pass_one_needs = _critical_flight_needs(pass_one)
    pass_one_attempt = _attempt_summary(
        day,
        pass_one,
        pass_number=1,
        included_leads=False,
        lead_candidate_count=0,
    )

    if not pass_one_usable:
        return _finalize_result(
            day,
            pass_one,
            attempts=(replace(pass_one_attempt, selected_as_final=True),),
            emergency_leads_enabled=(
                active_config.allow_leads_for_minimum_staffing
            ),
            disposition=(
                EmergencyPassDisposition.NOT_ATTEMPTED_NO_USABLE_SCHEDULE
                if active_config.allow_leads_for_minimum_staffing
                else EmergencyPassDisposition.NOT_ENABLED
            ),
            overall_runtime=max(0.0, monotonic() - overall_started_at),
        )

    if not pass_one_needs or not active_config.allow_leads_for_minimum_staffing:
        return _finalize_result(
            day,
            pass_one,
            attempts=(replace(pass_one_attempt, selected_as_final=True),),
            emergency_leads_enabled=(
                active_config.allow_leads_for_minimum_staffing
            ),
            disposition=(
                EmergencyPassDisposition.NOT_ENABLED
                if not active_config.allow_leads_for_minimum_staffing
                else EmergencyPassDisposition.NOT_NEEDED
            ),
            overall_runtime=max(0.0, monotonic() - overall_started_at),
        )

    pass_two, lead_candidate_count = _run_optimization_pass(
        day,
        active_config,
        include_leads=True,
        critical_needs=pass_one_needs,
    )
    pass_two_attempt = _attempt_summary(
        day,
        pass_two,
        pass_number=2,
        included_leads=True,
        lead_candidate_count=lead_candidate_count,
    )
    pass_two_lead_assignments: tuple[EmergencyLeadAssignmentResult, ...] = ()
    pass_two_lead_reporting_valid = True
    if _result_has_usable_schedule(day, pass_two):
        pass_two_lead_assignments, _ = _derive_emergency_lead_assignments(
            day,
            pass_two,
            ordinary_result=pass_one,
        )
        pass_two_lead_reporting_valid = (
            len(pass_two_lead_assignments)
            == _lead_assignment_count(day, pass_two)
        )
    adopt_pass_two, disposition, disposition_message = (
        _emergency_adoption_decision(
            day,
            pass_one,
            pass_two,
            pass_two_lead_assignments,
            lead_reporting_valid=pass_two_lead_reporting_valid,
        )
    )
    overall_runtime = max(0.0, monotonic() - overall_started_at)
    if not adopt_pass_two:
        return _finalize_result(
            day,
            pass_one,
            attempts=(
                replace(pass_one_attempt, selected_as_final=True),
                pass_two_attempt,
            ),
            emergency_leads_enabled=True,
            disposition=disposition,
            overall_runtime=overall_runtime,
            disposition_warning_message=disposition_message,
        )

    return _finalize_result(
        day,
        pass_two,
        attempts=(
            pass_one_attempt,
            replace(pass_two_attempt, selected_as_final=True),
        ),
        emergency_leads_enabled=True,
        disposition=disposition,
        overall_runtime=overall_runtime,
        ordinary_result=pass_one,
    )


def _run_optimization_pass(
    day: OperationalDay,
    config: OptimizerConfig,
    *,
    include_leads: bool,
    critical_needs: tuple[_CriticalFlightNeed, ...],
) -> tuple[OptimizationResult, int]:
    """Build and solve one fresh model without duplicating optimizer logic."""

    started_at = monotonic()
    candidates = build_candidate_assignments(
        day,
        config,
        include_leads=include_leads,
    )
    if include_leads:
        candidates = _filter_emergency_candidates(
            day, config, candidates, critical_needs
        )
    model_data = _build_model(
        day,
        config,
        candidates,
        include_leads=include_leads,
    )
    status, solver, objectives = _solve_lexicographically(
        model_data, config, started_at
    )
    runtime = max(0.0, monotonic() - started_at)
    result = (
        _empty_solver_result(status, objectives, runtime)
        if solver is None
        else _build_result(
            day,
            config,
            model_data,
            solver,
            status,
            objectives,
            runtime,
        )
    )
    return result, len(model_data.lead_decisions)


def _result_has_usable_schedule(
    day: OperationalDay, result: OptimizationResult
) -> bool:
    """Validate that a complete public schedule can safely be considered."""

    if result.status not in {OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE}:
        return False
    if len(result.flight_results) != len(day.flights):
        return False
    if tuple(item.flight for item in result.flight_results) != day.flights:
        return False
    if len({item.employee_id for item in result.employee_results}) != len(
        result.employee_results
    ):
        return False
    known_employee_ids = {
        employee.employee_id.strip().casefold() for employee in day.employees
    }
    reported_employee_ids = {
        employee.employee_id.strip().casefold()
        for employee in result.employee_results
    }
    if not reported_employee_ids <= known_employee_ids:
        return False
    if not all(
        len(set(flight.assigned_employee_ids)) == len(flight.assigned_employee_ids)
        and all(
            employee_id.strip().casefold() in known_employee_ids
            for employee_id in flight.assigned_employee_ids
        )
        for flight in result.flight_results
    ):
        return False
    assigned_counts: dict[str, int] = {}
    for flight in result.flight_results:
        for employee_id in flight.assigned_employee_ids:
            normalized_id = employee_id.strip().casefold()
            assigned_counts[normalized_id] = assigned_counts.get(normalized_id, 0) + 1
    if not set(assigned_counts) <= reported_employee_ids:
        return False
    if any(
        employee.flight_count
        != assigned_counts.get(employee.employee_id.strip().casefold(), 0)
        for employee in result.employee_results
    ):
        return False
    return all(
        flight.staffing_count == len(flight.assigned_employee_ids)
        and flight.minimum_met is (flight.staffing_count >= flight.minimum_staff)
        and flight.minimum_shortfall
        == max(0, flight.minimum_staff - flight.staffing_count)
        and flight.staffing_count <= flight.maximum_staff
        and (
            flight.push_covered is None and flight.close_covered is None
            if flight.flight_type is FlightType.ARRIVAL_ONLY
            else isinstance(flight.push_covered, bool)
            and isinstance(flight.close_covered, bool)
        )
        for flight in result.flight_results
    )


def _critical_operational_score(
    day: OperationalDay, result: OptimizationResult
) -> _CriticalOperationalScore | None:
    """Reconstruct every pre-Lead objective needed for safe pass adoption."""

    if not _result_has_usable_schedule(day, result):
        return None
    required_flights = tuple(
        flight
        for flight in result.flight_results
        if flight.flight_type is not FlightType.ARRIVAL_ONLY
        and flight.minimum_met
    )
    shortfalls = tuple(
        flight.minimum_shortfall for flight in result.flight_results
    )
    return _CriticalOperationalScore(
        minimum_staffed_flights=sum(
            flight.minimum_met for flight in result.flight_results
        ),
        qualification_compliant_flights=sum(
            bool(flight.push_covered) and bool(flight.close_covered)
            for flight in required_flights
        ),
        individual_qualification_coverage=sum(
            int(bool(flight.push_covered)) + int(bool(flight.close_covered))
            for flight in required_flights
        ),
        total_minimum_shortfall=sum(shortfalls),
        largest_minimum_shortfall=max(shortfalls, default=0),
        known_unsatisfied_breaks=sum(
            employee.break_status is BreakStatus.UNSATISFIED
            for employee in result.employee_results
        ),
    )


_LATER_OBJECTIVE_DIRECTIONS: tuple[tuple[str, bool], ...] = (
    ("preferred_staffed_flights", True),
    ("total_preferred_shortfall", False),
    ("partial_crew_individual_qualification_coverage", True),
    ("raw_flight_count_spread", False),
    ("total_pairwise_flight_count_difference", False),
    ("maximum_consecutive_flight_streak", False),
    ("total_employee_longest_streaks", False),
    ("total_shift_adjusted_flight_count_deviation", False),
    ("adjusted_workload_spread", False),
    ("total_pairwise_adjusted_workload_difference", False),
    ("total_continuity_retention", True),
)


def _has_proven_later_objective_improvement(
    ordinary_result: OptimizationResult,
    emergency_result: OptimizationResult,
) -> bool:
    """Compare later objectives by validated names, never by shifted positions."""

    ordinary = {item.name: item for item in ordinary_result.objective_values}
    emergency = {item.name: item for item in emergency_result.objective_values}
    for name, maximize in _LATER_OBJECTIVE_DIRECTIONS:
        ordinary_value = ordinary.get(name)
        emergency_value = emergency.get(name)
        if ordinary_value is None or emergency_value is None:
            return False
        if not ordinary_value.proven_optimal or not emergency_value.proven_optimal:
            return False
        if ordinary_value.value == emergency_value.value:
            continue
        return (
            emergency_value.value > ordinary_value.value
            if maximize
            else emergency_value.value < ordinary_value.value
        )
    return False


def _emergency_adoption_decision(
    day: OperationalDay,
    ordinary_result: OptimizationResult,
    emergency_result: OptimizationResult,
    lead_assignments: tuple[EmergencyLeadAssignmentResult, ...],
    *,
    lead_reporting_valid: bool,
) -> tuple[bool, EmergencyPassDisposition, str | None]:
    """Choose Pass 2 only when its reconstructed critical outcome is safe."""

    ordinary_score = _critical_operational_score(day, ordinary_result)
    emergency_score = _critical_operational_score(day, emergency_result)
    if (
        ordinary_score is None
        or emergency_score is None
        or not lead_reporting_valid
    ):
        return (
            False,
            EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_UNUSABLE,
            "Emergency recovery returned no safely comparable usable "
            "schedule; Pass 1 was retained.",
        )
    if emergency_score.comparison_key < ordinary_score.comparison_key:
        return (
            False,
            EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_WORSE,
            "Emergency recovery had a worse critical operational outcome; "
            "Pass 1 was retained.",
        )
    if emergency_score.comparison_key > ordinary_score.comparison_key:
        adopted = True
    else:
        adopted = bool(lead_assignments) or _has_proven_later_objective_improvement(
            ordinary_result, emergency_result
        )
    if not adopted:
        return (
            False,
            EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT,
            "Emergency recovery did not improve the safely comparable "
            "schedule; Pass 1 was retained.",
        )
    has_remaining_shortage = bool(_critical_flight_needs(emergency_result))
    return (
        True,
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
        if has_remaining_shortage
        else EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED,
        None,
    )


def _critical_flight_needs(
    result: OptimizationResult,
) -> tuple[_CriticalFlightNeed, ...]:
    """Independently detect only minimum and minimum-team qualification defects."""

    needs: list[_CriticalFlightNeed] = []
    for flight_index, flight_result in enumerate(result.flight_results):
        below_minimum = not flight_result.minimum_met
        minimum_qualified_team = (
            flight_result.minimum_met
            and flight_result.flight_type is not FlightType.ARRIVAL_ONLY
        )
        missing_push = minimum_qualified_team and not flight_result.push_covered
        missing_close = minimum_qualified_team and not flight_result.close_covered
        if below_minimum or missing_push or missing_close:
            needs.append(
                _CriticalFlightNeed(
                    flight_index=flight_index,
                    below_minimum=below_minimum,
                    missing_push=missing_push,
                    missing_close=missing_close,
                )
            )
    return tuple(needs)


def _critical_shortage_count(
    needs: tuple[_CriticalFlightNeed, ...],
) -> int:
    return sum(
        int(need.below_minimum)
        + int(need.missing_push)
        + int(need.missing_close)
        for need in needs
    )


def _result_assignment_uses_lead_shift(
    day: OperationalDay,
    employee_index: int,
    flight_result: FlightAssignmentResult,
) -> bool:
    employee_id = day.employees[employee_index].employee_id.strip().casefold()
    return any(
        shift.employee_id.strip().casefold() == employee_id
        and shift.normalized_role is OperationalRole.RAMP_LEAD
        and shift.start <= flight_result.work_start
        and flight_result.work_end <= shift.end
        for shift in day.employee_shifts
    )


def _lead_assignment_count(
    day: OperationalDay, result: OptimizationResult
) -> int:
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    count = 0
    for flight_result in result.flight_results:
        for employee_id in flight_result.assigned_employee_ids:
            employee_index = employee_indices.get(employee_id.strip().casefold())
            if employee_index is not None and _result_assignment_uses_lead_shift(
                day, employee_index, flight_result
            ):
                count += 1
    return count


def _attempt_summary(
    day: OperationalDay,
    result: OptimizationResult,
    *,
    pass_number: int,
    included_leads: bool,
    lead_candidate_count: int,
) -> OptimizationAttemptSummary:
    needs = _critical_flight_needs(result)
    qualification_compliant_flights = sum(
        flight.minimum_met
        and flight.flight_type is not FlightType.ARRIVAL_ONLY
        and bool(flight.push_covered)
        and bool(flight.close_covered)
        for flight in result.flight_results
    )
    return OptimizationAttemptSummary(
        included_leads=included_leads,
        status=result.status,
        minimum_staffed_flights=sum(
            flight.minimum_met for flight in result.flight_results
        ),
        qualification_compliant_flights=qualification_compliant_flights,
        lead_assignments=_lead_assignment_count(day, result),
        critical_shortage_count=_critical_shortage_count(needs),
        lead_candidate_count=lead_candidate_count,
        solver_runtime_seconds=result.solver_runtime_seconds,
        pass_number=pass_number,
        attempt_label=(
            "RAMP_AGENT_ONLY" if pass_number == 1 else "EMERGENCY_LEAD_RECOVERY"
        ),
        usable_schedule=_result_has_usable_schedule(day, result),
        known_unsatisfied_required_break_count=sum(
            employee.break_status is BreakStatus.UNSATISFIED
            for employee in result.employee_results
        ),
        objective_stages_completed=sum(
            objective.proven_optimal for objective in result.objective_values
        ),
        all_objectives_proven_optimal=(
            bool(result.objective_values)
            and all(
                objective.proven_optimal
                for objective in result.objective_values
            )
        ),
    )


def _flight_label(flight_result: FlightAssignmentResult) -> str:
    return (
        flight_result.flight.departure_flight_number
        or flight_result.flight.arrival_flight_number
        or "unnumbered flight"
    )


def _derive_emergency_lead_assignments(
    day: OperationalDay,
    result: OptimizationResult,
    *,
    ordinary_result: OptimizationResult,
) -> tuple[
    tuple[EmergencyLeadAssignmentResult, ...],
    tuple[ScheduleWarning, ...],
]:
    """Recompute each selected Lead's critical contribution from final crews."""

    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    assignments: list[EmergencyLeadAssignmentResult] = []
    warnings: list[ScheduleWarning] = []
    assert len(ordinary_result.flight_results) == len(result.flight_results)
    for flight_index, flight_result in enumerate(result.flight_results):
        ordinary_flight = ordinary_result.flight_results[flight_index]
        assigned_indices = tuple(
            employee_indices[employee_id.strip().casefold()]
            for employee_id in flight_result.assigned_employee_ids
        )
        for employee_index in assigned_indices:
            if not _result_assignment_uses_lead_shift(
                day, employee_index, flight_result
            ):
                continue
            employee = day.employees[employee_index]
            other_indices = tuple(
                index for index in assigned_indices if index != employee_index
            )
            reasons: list[EmergencyLeadReason] = []
            if (
                not ordinary_flight.minimum_met
                and flight_result.staffing_count <= flight_result.minimum_staff
            ):
                reasons.append(EmergencyLeadReason.MINIMUM_STAFFING)
            if (
                flight_result.minimum_met
                and flight_result.flight_type is not FlightType.ARRIVAL_ONLY
            ):
                if (
                    Qualification.PUSH in employee.qualifications
                    and (
                        not ordinary_flight.minimum_met
                        or not bool(ordinary_flight.push_covered)
                    )
                    and not any(
                        Qualification.PUSH in day.employees[index].qualifications
                        for index in other_indices
                    )
                ):
                    reasons.append(EmergencyLeadReason.PUSH_QUALIFICATION)
                if (
                    Qualification.CLOSE_OUT in employee.qualifications
                    and (
                        not ordinary_flight.minimum_met
                        or not bool(ordinary_flight.close_covered)
                    )
                    and not any(
                        Qualification.CLOSE_OUT
                        in day.employees[index].qualifications
                        for index in other_indices
                    )
                ):
                    reasons.append(EmergencyLeadReason.CLOSE_QUALIFICATION)
            if not reasons:
                continue

            label = _flight_label(flight_result)
            reason_text = {
                EmergencyLeadReason.MINIMUM_STAFFING: (
                    f"Ramp Agents alone could not provide the required "
                    f"{flight_result.minimum_staff} employees"
                ),
                EmergencyLeadReason.PUSH_QUALIFICATION: (
                    "the crew otherwise had no push-qualified employee"
                ),
                EmergencyLeadReason.CLOSE_QUALIFICATION: (
                    "the crew otherwise had no close-out-qualified employee"
                ),
            }
            message = (
                f"Lead {employee.name} was assigned to {label} because "
                + "; and ".join(reason_text[reason] for reason in reasons)
                + "."
            )
            assignments.append(
                EmergencyLeadAssignmentResult(
                    employee_id=employee.employee_id,
                    flight=flight_result.flight,
                    reasons=tuple(reasons),
                    message=message,
                )
            )
            warnings.append(
                ScheduleWarning(
                    code=WarningCode.EMERGENCY_LEAD_USED,
                    severity=WarningSeverity.INFO,
                    message=message,
                    arrival_flight_number=(
                        flight_result.flight.arrival_flight_number
                    ),
                    departure_flight_number=(
                        flight_result.flight.departure_flight_number
                    ),
                    employee_id=employee.employee_id,
                )
            )
    return tuple(assignments), tuple(warnings)


def _unresolved_flight_needs(
    result: OptimizationResult,
) -> tuple[_CriticalFlightNeed, ...]:
    """Return every final flight issue, including qualifications below minimum."""

    needs: list[_CriticalFlightNeed] = []
    for flight_index, flight in enumerate(result.flight_results):
        qualification_required = flight.flight_type is not FlightType.ARRIVAL_ONLY
        need = _CriticalFlightNeed(
            flight_index=flight_index,
            below_minimum=not flight.minimum_met,
            missing_push=qualification_required and not bool(flight.push_covered),
            missing_close=qualification_required and not bool(flight.close_covered),
        )
        if need.below_minimum or need.missing_push or need.missing_close:
            needs.append(need)
    return tuple(needs)


def _manual_intervention_warnings(
    result: OptimizationResult,
    needs: tuple[_CriticalFlightNeed, ...],
) -> tuple[ScheduleWarning, ...]:
    warnings: list[ScheduleWarning] = []
    for need in needs:
        flight_result = result.flight_results[need.flight_index]
        defects: list[str] = []
        if need.below_minimum:
            defects.append("minimum staffing")
        if need.missing_push:
            defects.append("push qualification")
        if need.missing_close:
            defects.append("close-out qualification")
        warnings.append(
            ScheduleWarning(
                code=WarningCode.MANUAL_INTERVENTION_REQUIRED,
                severity=WarningSeverity.CRITICAL,
                message=(
                    f"{_flight_label(flight_result)} requires manual intervention: "
                    + ", ".join(defects)
                    + "."
                ),
                arrival_flight_number=(
                    flight_result.flight.arrival_flight_number
                ),
                departure_flight_number=(
                    flight_result.flight.departure_flight_number
                ),
            )
        )
    return tuple(warnings)


def _deduplicate_warnings(
    warnings: tuple[ScheduleWarning, ...],
) -> tuple[ScheduleWarning, ...]:
    """Deduplicate exact structured subjects while retaining stable order."""

    seen: set[tuple[WarningCode, str | None, str | None, str | None]] = set()
    unique: list[ScheduleWarning] = []
    for warning in warnings:
        identity = (
            warning.code,
            warning.employee_id,
            warning.arrival_flight_number,
            warning.departure_flight_number,
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(warning)
    return tuple(unique)


def _derive_schedule_summary(
    result: OptimizationResult,
    warnings: tuple[ScheduleWarning, ...],
    lead_assignments: tuple[EmergencyLeadAssignmentResult, ...],
) -> ScheduleSummary:
    """Build the public summary strictly from final public result records."""

    required_flights = tuple(
        flight
        for flight in result.flight_results
        if flight.flight_type is not FlightType.ARRIVAL_ONLY
    )
    return ScheduleSummary(
        total_flights=len(result.flight_results),
        minimum_staffed_flights=sum(
            flight.minimum_met for flight in result.flight_results
        ),
        below_minimum_flights=sum(
            not flight.minimum_met for flight in result.flight_results
        ),
        preferred_staffed_flights=sum(
            flight.preferred_met for flight in result.flight_results
        ),
        qualification_required_flights=len(required_flights),
        qualification_compliant_flights=sum(
            bool(flight.push_covered) and bool(flight.close_covered)
            for flight in required_flights
        ),
        missing_push_flights=sum(
            not bool(flight.push_covered) for flight in required_flights
        ),
        missing_close_out_flights=sum(
            not bool(flight.close_covered) for flight in required_flights
        ),
        participating_employee_count=sum(
            employee.flight_count > 0 for employee in result.employee_results
        ),
        employees_with_satisfied_break=sum(
            employee.break_status is BreakStatus.SATISFIED
            for employee in result.employee_results
        ),
        employees_with_unsatisfied_break=sum(
            employee.break_status is BreakStatus.UNSATISFIED
            for employee in result.employee_results
        ),
        employees_with_nonevaluable_break=sum(
            employee.break_status
            is BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
            for employee in result.employee_results
        ),
        total_assignments=sum(
            flight.staffing_count for flight in result.flight_results
        ),
        emergency_lead_assignments=len(lead_assignments),
        critical_warning_count=sum(
            warning.severity is WarningSeverity.CRITICAL for warning in warnings
        ),
        warning_count=len(warnings),
        all_objectives_proven_optimal=(
            bool(result.objective_values)
            and all(
                objective.proven_optimal for objective in result.objective_values
            )
        ),
    )


def _finalize_result(
    day: OperationalDay,
    result: OptimizationResult,
    *,
    attempts: tuple[OptimizationAttemptSummary, ...],
    emergency_leads_enabled: bool,
    disposition: EmergencyPassDisposition,
    overall_runtime: float,
    ordinary_result: OptimizationResult | None = None,
    disposition_warning_message: str | None = None,
) -> OptimizationResult:
    """Attach deterministic operational reporting to the selected raw result."""

    usable = _result_has_usable_schedule(day, result)
    lead_assignments: tuple[EmergencyLeadAssignmentResult, ...] = ()
    lead_warnings: tuple[ScheduleWarning, ...] = ()
    if usable and ordinary_result is not None:
        lead_assignments, lead_warnings = _derive_emergency_lead_assignments(
            day,
            result,
            ordinary_result=ordinary_result,
        )

    unresolved_needs = _unresolved_flight_needs(result) if usable else ()
    break_shortage = any(
        employee.break_status is BreakStatus.UNSATISFIED
        for employee in result.employee_results
    )
    disposition_warnings: tuple[ScheduleWarning, ...] = ()
    if disposition_warning_message is not None:
        disposition_warnings = (
            ScheduleWarning(
                code=WarningCode.EMERGENCY_RECOVERY_NOT_ADOPTED,
                severity=WarningSeverity.WARNING,
                message=disposition_warning_message,
            ),
        )

    reporting_warnings: list[ScheduleWarning] = []
    if not usable:
        reporting_warnings.append(
            ScheduleWarning(
                code=WarningCode.NO_USABLE_SCHEDULE,
                severity=WarningSeverity.CRITICAL,
                message="No usable schedule recommendation was generated.",
            )
        )
    elif not result.objective_values or not all(
        objective.proven_optimal for objective in result.objective_values
    ):
        reporting_warnings.append(
            ScheduleWarning(
                code=WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL,
                severity=WarningSeverity.WARNING,
                message=(
                    "A usable schedule was returned, but not every optimization "
                    "stage was proven optimal."
                ),
            )
        )

    warnings = _deduplicate_warnings(
        result.warnings
        + lead_warnings
        + disposition_warnings
        + _manual_intervention_warnings(result, unresolved_needs)
        + tuple(reporting_warnings)
    )
    readiness = (
        OperationalReadinessStatus.NO_USABLE_SCHEDULE
        if not usable
        else OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
        if unresolved_needs or break_shortage
        else OperationalReadinessStatus.READY_WITH_WARNINGS
        if warnings
        else OperationalReadinessStatus.READY
    )
    emergency_status = (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
        if unresolved_needs
        else EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE
        if lead_assignments
        else EmergencyStaffingStatus.NORMAL_SCHEDULE
    )
    finalized = replace(
        result,
        attempts=attempts,
        warnings=warnings,
        emergency_lead_staffing_used=bool(lead_assignments),
        emergency_leads_enabled=emergency_leads_enabled,
        emergency_staffing_status=emergency_status,
        emergency_pass_disposition=disposition,
        lead_assignments=lead_assignments,
        solver_runtime_seconds=overall_runtime,
        operational_readiness=readiness,
    )
    return replace(
        finalized,
        schedule_summary=_derive_schedule_summary(
            finalized, warnings, lead_assignments
        ),
    )


def optimize_minimum_staffing(
    day: OperationalDay, config: OptimizerConfig | None = None
) -> OptimizationResult:
    """Backward-compatible alias for :func:`optimize_flight_assignments`."""

    return optimize_flight_assignments(day, config)


def _build_model(
    day: OperationalDay,
    config: OptimizerConfig,
    candidates: tuple[CandidateAssignment, ...],
    *,
    include_leads: bool = False,
) -> _ModelData:
    model = cp_model.CpModel()
    facts = tuple(
        derive_flight_operational_facts(flight, config) for flight in day.flights
    )
    requirements = tuple(
        staffing_requirements_for(flight, config) for flight in day.flights
    )
    candidate_indices = _index_candidates(day, candidates)

    # x[e, f] exists only for a legal, non-fixed candidate. Fixed assignments
    # are constants and are never optional decisions.
    decisions = {
        pair: model.new_bool_var(f"x_e{pair[0]}_f{pair[1]}")
        for pair in candidate_indices
    }
    lead_decisions = {
        pair: decision
        for pair, decision in decisions.items()
        if _pair_uses_lead_shift(day, facts, *pair)
    }
    fixed_employee_indices = _fixed_employee_indices_by_flight(day)
    _add_candidate_overlap_constraints(model, decisions, facts)

    staff_counts: list[cp_model.IntVar] = []
    minimum_met: list[cp_model.IntVar] = []
    minimum_shortfalls: list[cp_model.IntVar] = []
    preferred_met: list[cp_model.IntVar] = []
    preferred_shortfalls: list[cp_model.IntVar] = []
    push_covered: list[cp_model.IntVar | None] = []
    close_covered: list[cp_model.IntVar | None] = []
    qualification_compliant: list[cp_model.IntVar | None] = []
    minimum_staffed_qualification_compliant: list[cp_model.IntVar] = []
    minimum_staffed_qualification_coverage: list[cp_model.IntVar] = []
    partial_crew_qualification_coverage: list[cp_model.IntVar] = []

    for flight_index, requirement in enumerate(requirements):
        fixed_count = len(fixed_employee_indices[flight_index])
        flight_decisions = [
            decision
            for (employee_index, candidate_flight_index), decision in decisions.items()
            if candidate_flight_index == flight_index
        ]
        staff_count = model.new_int_var(
            fixed_count,
            requirement.maximum,
            f"staff_count_f{flight_index}",
        )
        model.add(staff_count == fixed_count + sum(flight_decisions))
        model.add(staff_count <= requirement.maximum)
        staff_counts.append(staff_count)

        # minimum_met[f] is exact in both directions, and the shortfall records
        # max(0, minimum - staff_count) without making minimum a hard constraint.
        minimum_indicator = _add_exact_threshold_indicator(
            model,
            staff_count,
            requirement.minimum,
            f"minimum_met_f{flight_index}",
        )
        minimum_shortfall = model.new_int_var(
            0,
            requirement.minimum,
            f"minimum_shortfall_f{flight_index}",
        )
        model.add_max_equality(
            minimum_shortfall,
            [requirement.minimum - staff_count, 0],
        )
        minimum_met.append(minimum_indicator)
        minimum_shortfalls.append(minimum_shortfall)

        # preferred_met[f] and preferred_shortfall[f] describe completion of
        # the desired crew. Preferred currently equals the hard maximum.
        preferred_indicator = _add_exact_threshold_indicator(
            model,
            staff_count,
            requirement.preferred,
            f"preferred_met_f{flight_index}",
        )
        preferred_shortfall = model.new_int_var(
            0,
            requirement.preferred,
            f"preferred_shortfall_f{flight_index}",
        )
        model.add(preferred_shortfall == requirement.preferred - staff_count)
        preferred_met.append(preferred_indicator)
        preferred_shortfalls.append(preferred_shortfall)

        if facts[flight_index].flight_type is FlightType.ARRIVAL_ONLY:
            push_covered.append(None)
            close_covered.append(None)
            qualification_compliant.append(None)
            continue

        push_indicator = _add_exact_qualification_indicator(
            model,
            day,
            decisions,
            fixed_employee_indices[flight_index],
            flight_index,
            Qualification.PUSH,
            f"push_covered_f{flight_index}",
        )
        close_indicator = _add_exact_qualification_indicator(
            model,
            day,
            decisions,
            fixed_employee_indices[flight_index],
            flight_index,
            Qualification.CLOSE_OUT,
            f"close_covered_f{flight_index}",
        )
        compliant_indicator = _add_exact_and_indicator(
            model,
            push_indicator,
            close_indicator,
            f"qualification_compliant_f{flight_index}",
        )
        push_covered.append(push_indicator)
        close_covered.append(close_indicator)
        qualification_compliant.append(compliant_indicator)

        minimum_compliant = _add_exact_and_indicator(
            model,
            minimum_indicator,
            compliant_indicator,
            f"minimum_staffed_qualification_compliant_f{flight_index}",
        )
        minimum_push = _add_exact_and_indicator(
            model,
            minimum_indicator,
            push_indicator,
            f"minimum_staffed_push_covered_f{flight_index}",
        )
        minimum_close = _add_exact_and_indicator(
            model,
            minimum_indicator,
            close_indicator,
            f"minimum_staffed_close_covered_f{flight_index}",
        )
        partial_push = _add_exact_below_minimum_coverage_indicator(
            model,
            minimum_indicator,
            push_indicator,
            f"partial_crew_push_covered_f{flight_index}",
        )
        partial_close = _add_exact_below_minimum_coverage_indicator(
            model,
            minimum_indicator,
            close_indicator,
            f"partial_crew_close_covered_f{flight_index}",
        )
        minimum_staffed_qualification_compliant.append(minimum_compliant)
        minimum_staffed_qualification_coverage.extend(
            (minimum_push, minimum_close)
        )
        partial_crew_qualification_coverage.extend(
            (partial_push, partial_close)
        )

    largest_shortfall_bound = max(
        (requirement.minimum for requirement in requirements), default=0
    )
    largest_minimum_shortfall = model.new_int_var(
        0,
        largest_shortfall_bound,
        "largest_minimum_shortfall",
    )
    if minimum_shortfalls:
        model.add_max_equality(largest_minimum_shortfall, minimum_shortfalls)
    else:
        model.add(largest_minimum_shortfall == 0)

    (
        included_employee_indices,
        break_evaluable,
        break_achieved,
        known_unsatisfied_break,
        break_gap_variables,
    ) = _add_break_model(
        model,
        day,
        config,
        decisions,
        fixed_employee_indices,
        facts,
        include_leads=include_leads,
    )
    ordinary_employee_indices = _included_ordinary_employee_indices(day, config)
    (
        fairness_employee_indices,
        fairness_flight_counts,
        highest_flight_count,
        lowest_flight_count,
        flight_count_spread,
        pairwise_flight_count_differences,
        total_pairwise_flight_count_difference,
    ) = _add_fairness_model(
        model,
        day,
        decisions,
        fixed_employee_indices,
        ordinary_employee_indices,
    )
    (
        streak_predecessor_arcs,
        streak_run_lengths,
        employee_longest_streaks,
        maximum_consecutive_flight_streak,
        total_employee_longest_streaks,
    ) = _add_consecutive_streak_model(
        model,
        day,
        config,
        decisions,
        fixed_employee_indices,
        facts,
        fairness_employee_indices,
    )
    (
        fairness_shift_minutes,
        total_fairness_shift_minutes,
        total_fairness_assignment_count,
        shift_adjusted_deviations,
        total_shift_adjusted_deviation,
    ) = _add_shift_length_adjustment_model(
        model,
        day,
        config,
        fairness_employee_indices,
        fairness_flight_counts,
    )
    (
        three_person_staffing,
        assigned_to_three_person_flight,
        adjusted_workload_units,
        highest_adjusted_workload,
        lowest_adjusted_workload,
        adjusted_workload_spread,
        pairwise_adjusted_workload_differences,
        total_pairwise_adjusted_workload_difference,
    ) = _add_adjusted_workload_model(
        model,
        day,
        config,
        decisions,
        fixed_employee_indices,
        tuple(staff_counts),
        facts,
        fairness_employee_indices,
    )
    (
        continuity_flight_pairs,
        retained_employee_transitions,
        total_continuity_retention,
    ) = _add_continuity_model(
        model,
        day,
        config,
        decisions,
        fixed_employee_indices,
        facts,
        fairness_employee_indices,
    )

    _constrain_emergency_lead_value(
        model,
        day,
        lead_decisions,
        decisions,
        fixed_employee_indices,
        tuple(staff_counts),
        tuple(minimum_met),
        facts,
        requirements,
    )
    total_lead_assignments = model.new_int_var(
        0,
        len(lead_decisions),
        "total_lead_assignments",
    )
    model.add(total_lead_assignments == sum(lead_decisions.values()))

    return _ModelData(
        model=model,
        decisions=decisions,
        facts=facts,
        requirements=requirements,
        fixed_employee_indices=fixed_employee_indices,
        staff_counts=tuple(staff_counts),
        minimum_met=tuple(minimum_met),
        minimum_shortfalls=tuple(minimum_shortfalls),
        preferred_met=tuple(preferred_met),
        preferred_shortfalls=tuple(preferred_shortfalls),
        largest_minimum_shortfall=largest_minimum_shortfall,
        push_covered=tuple(push_covered),
        close_covered=tuple(close_covered),
        qualification_compliant=tuple(qualification_compliant),
        minimum_staffed_qualification_compliant=tuple(
            minimum_staffed_qualification_compliant
        ),
        minimum_staffed_qualification_coverage=tuple(
            minimum_staffed_qualification_coverage
        ),
        partial_crew_qualification_coverage=tuple(
            partial_crew_qualification_coverage
        ),
        included_employee_indices=included_employee_indices,
        break_evaluable=break_evaluable,
        break_achieved=break_achieved,
        known_unsatisfied_break=known_unsatisfied_break,
        break_gap_variables=break_gap_variables,
        fairness_employee_indices=fairness_employee_indices,
        fairness_flight_counts=fairness_flight_counts,
        highest_flight_count=highest_flight_count,
        lowest_flight_count=lowest_flight_count,
        flight_count_spread=flight_count_spread,
        pairwise_flight_count_differences=(
            pairwise_flight_count_differences
        ),
        total_pairwise_flight_count_difference=(
            total_pairwise_flight_count_difference
        ),
        streak_predecessor_arcs=streak_predecessor_arcs,
        streak_run_lengths=streak_run_lengths,
        employee_longest_streaks=employee_longest_streaks,
        maximum_consecutive_flight_streak=(
            maximum_consecutive_flight_streak
        ),
        total_employee_longest_streaks=total_employee_longest_streaks,
        fairness_shift_minutes=fairness_shift_minutes,
        total_fairness_shift_minutes=total_fairness_shift_minutes,
        total_fairness_assignment_count=total_fairness_assignment_count,
        shift_adjusted_deviations=shift_adjusted_deviations,
        total_shift_adjusted_deviation=total_shift_adjusted_deviation,
        three_person_staffing=three_person_staffing,
        assigned_to_three_person_flight=assigned_to_three_person_flight,
        adjusted_workload_units=adjusted_workload_units,
        highest_adjusted_workload=highest_adjusted_workload,
        lowest_adjusted_workload=lowest_adjusted_workload,
        adjusted_workload_spread=adjusted_workload_spread,
        pairwise_adjusted_workload_differences=(
            pairwise_adjusted_workload_differences
        ),
        total_pairwise_adjusted_workload_difference=(
            total_pairwise_adjusted_workload_difference
        ),
        continuity_flight_pairs=continuity_flight_pairs,
        retained_employee_transitions=retained_employee_transitions,
        total_continuity_retention=total_continuity_retention,
        include_leads=include_leads,
        lead_decisions=lead_decisions,
        total_lead_assignments=total_lead_assignments,
    )


def _index_candidates(
    day: OperationalDay, candidates: tuple[CandidateAssignment, ...]
) -> tuple[tuple[int, int], ...]:
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    pairs: list[tuple[int, int]] = []
    for candidate in candidates:
        employee_index = employee_indices[candidate.employee_id.strip().casefold()]
        flight_index = day.flights.index(candidate.flight)
        pairs.append((employee_index, flight_index))
    return tuple(pairs)


def _fixed_employee_indices_by_flight(
    day: OperationalDay,
) -> tuple[tuple[int, ...], ...]:
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    fixed_by_flight: list[list[int]] = [[] for _ in day.flights]
    for fixed in day.fixed_assignments:
        employee_index = employee_indices[fixed.employee_id.strip().casefold()]
        flight_index = day.flights.index(fixed.flight)
        fixed_by_flight[flight_index].append(employee_index)
    return tuple(tuple(sorted(indices)) for indices in fixed_by_flight)


def _add_candidate_overlap_constraints(
    model: cp_model.CpModel,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    facts: tuple[FlightOperationalFacts, ...],
) -> None:
    flights_by_employee: dict[int, list[int]] = {}
    for employee_index, flight_index in decisions:
        flights_by_employee.setdefault(employee_index, []).append(flight_index)

    for employee_index, flight_indices in flights_by_employee.items():
        for first_flight, second_flight in combinations(flight_indices, 2):
            first = facts[first_flight]
            second = facts[second_flight]
            if intervals_overlap(
                first.work_start,
                first.work_end,
                second.work_start,
                second.work_end,
            ):
                model.add(
                    decisions[(employee_index, first_flight)]
                    + decisions[(employee_index, second_flight)]
                    <= 1
                )


def _add_break_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    config: OptimizerConfig,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    facts: tuple[FlightOperationalFacts, ...],
    *,
    include_leads: bool = False,
) -> tuple[
    tuple[int, ...],
    tuple[cp_model.IntVar | None, ...],
    tuple[cp_model.IntVar | None, ...],
    tuple[cp_model.IntVar | None, ...],
    dict[tuple[int, int, int], cp_model.IntVar],
]:
    """Add exact endpoint-derived break indicators without minute indexing."""

    included_employee_indices = _included_employee_indices(
        day, config, include_leads=include_leads
    )
    included_employee_set = set(included_employee_indices)
    assignment_values = _assignment_values_by_employee(
        day,
        decisions,
        fixed_employee_indices,
    )
    break_evaluable: list[cp_model.IntVar | None] = []
    break_achieved: list[cp_model.IntVar | None] = []
    known_unsatisfied_break: list[cp_model.IntVar | None] = []
    break_gap_variables: dict[tuple[int, int, int], cp_model.IntVar] = {}
    required_duration = timedelta(minutes=config.required_break_minutes)

    for employee_index, employee in enumerate(day.employees):
        if employee_index not in included_employee_set:
            break_evaluable.append(None)
            break_achieved.append(None)
            known_unsatisfied_break.append(None)
            continue

        possible_assignments = assignment_values[employee_index]
        assignment_count = sum(possible_assignments.values())
        evaluable = model.new_bool_var(f"break_evaluable_e{employee_index}")
        model.add(assignment_count >= 2).only_enforce_if(evaluable)
        model.add(assignment_count <= 1).only_enforce_if(evaluable.Not())

        ordered_flight_indices = sorted(
            possible_assignments,
            key=lambda flight_index: (
                facts[flight_index].work_start,
                facts[flight_index].work_end,
                flight_index,
            ),
        )
        assignment_prefix_counts: list[cp_model.IntVar | int] = [0]
        for position, flight_index in enumerate(ordered_flight_indices, start=1):
            prefix_count = model.new_int_var(
                0,
                position,
                f"assignment_prefix_count_e{employee_index}_p{position}",
            )
            model.add(
                prefix_count
                == assignment_prefix_counts[-1]
                + possible_assignments[flight_index]
            )
            assignment_prefix_counts.append(prefix_count)

        employee_gap_variables: list[cp_model.IntVar] = []
        for earlier_position, earlier_flight_index in enumerate(
            ordered_flight_indices
        ):
            earlier = facts[earlier_flight_index]
            for later_position in range(
                earlier_position + 1, len(ordered_flight_indices)
            ):
                later_flight_index = ordered_flight_indices[later_position]
                later = facts[later_flight_index]
                gap_duration = later.work_start - earlier.work_end
                if gap_duration < required_duration:
                    continue
                if not _one_eligible_shift_contains_assignment_span(
                    employee,
                    day,
                    config,
                    earlier.work_start,
                    later.work_end,
                    include_leads=include_leads,
                ):
                    continue

                intervening_assignment_count = (
                    assignment_prefix_counts[later_position]
                    - assignment_prefix_counts[earlier_position + 1]
                )
                gap_variable = _add_exact_gap_indicator(
                    model,
                    possible_assignments[earlier_flight_index],
                    possible_assignments[later_flight_index],
                    intervening_assignment_count,
                    (
                        f"qualifying_break_gap_e{employee_index}_"
                        f"f{earlier_flight_index}_f{later_flight_index}"
                    ),
                )
                break_gap_variables[
                    (employee_index, earlier_flight_index, later_flight_index)
                ] = gap_variable
                employee_gap_variables.append(gap_variable)

        achieved = _add_exact_any_indicator(
            model,
            tuple(employee_gap_variables),
            f"break_achieved_e{employee_index}",
        )
        unsatisfied = model.new_bool_var(
            f"known_unsatisfied_break_e{employee_index}"
        )
        model.add(unsatisfied <= evaluable)
        model.add(unsatisfied + achieved <= 1)
        model.add(unsatisfied >= evaluable - achieved)
        break_evaluable.append(evaluable)
        break_achieved.append(achieved)
        known_unsatisfied_break.append(unsatisfied)

    return (
        included_employee_indices,
        tuple(break_evaluable),
        tuple(break_achieved),
        tuple(known_unsatisfied_break),
        break_gap_variables,
    )


def _included_ordinary_employee_indices(
    day: OperationalDay,
    config: OptimizerConfig,
) -> tuple[int, ...]:
    """Return enabled employees represented by the resolved ordinary role policy."""

    return tuple(
        employee_index
        for employee_index, employee in enumerate(day.employees)
        if employee.enabled
        and any(
            shift.employee_id.strip().casefold()
            == employee.employee_id.strip().casefold()
            and role_is_assignment_eligible(
                shift.normalized_role,
                include_leads=False,
                allow_trainees=config.allow_trainees_for_assignments,
                allow_possible_ramp_support=(
                    config.allow_possible_ramp_support_for_assignments
                ),
            )
            for shift in day.employee_shifts
        )
    )


def _assignment_values_by_employee(
    day: OperationalDay,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
) -> tuple[dict[int, cp_model.IntVar | int], ...]:
    values: list[dict[int, cp_model.IntVar | int]] = [
        {} for _ in day.employees
    ]
    for flight_index, employee_indices in enumerate(fixed_employee_indices):
        for employee_index in employee_indices:
            values[employee_index][flight_index] = 1
    for (employee_index, flight_index), decision in decisions.items():
        values[employee_index][flight_index] = decision
    return tuple(values)


def _add_fairness_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    included_employee_indices: tuple[int, ...],
) -> tuple[
    tuple[int, ...],
    tuple[cp_model.IntVar | None, ...],
    cp_model.IntVar,
    cp_model.IntVar,
    cp_model.IntVar,
    tuple[cp_model.IntVar, ...],
    cp_model.IntVar,
]:
    """Add exact raw-count extrema and employee-pair differences."""

    fixed_counts = [0 for _ in day.employees]
    for employee_indices in fixed_employee_indices:
        for employee_index in employee_indices:
            fixed_counts[employee_index] += 1

    employee_indices_with_candidates = {
        employee_index for employee_index, _ in decisions
    }
    employee_indices_with_fixed = {
        employee_index
        for employee_index, fixed_count in enumerate(fixed_counts)
        if fixed_count
    }
    possible_participants = (
        employee_indices_with_candidates | employee_indices_with_fixed
    )
    fairness_employee_indices = tuple(
        employee_index
        for employee_index in included_employee_indices
        if employee_index in possible_participants
    )
    fairness_employee_set = set(fairness_employee_indices)

    fairness_flight_counts: list[cp_model.IntVar | None] = []
    participating_counts: list[cp_model.IntVar] = []
    maximum_possible_count = len(day.flights)
    for employee_index in range(len(day.employees)):
        if employee_index not in fairness_employee_set:
            fairness_flight_counts.append(None)
            continue
        employee_decisions = [
            decision
            for (candidate_employee_index, _), decision in decisions.items()
            if candidate_employee_index == employee_index
        ]
        flight_count = model.new_int_var(
            fixed_counts[employee_index],
            maximum_possible_count,
            f"raw_flight_count_e{employee_index}",
        )
        model.add(
            flight_count
            == fixed_counts[employee_index] + sum(employee_decisions)
        )
        fairness_flight_counts.append(flight_count)
        participating_counts.append(flight_count)

    highest_flight_count = model.new_int_var(
        0,
        maximum_possible_count,
        "highest_raw_flight_count",
    )
    lowest_flight_count = model.new_int_var(
        0,
        maximum_possible_count,
        "lowest_raw_flight_count",
    )
    if participating_counts:
        model.add_max_equality(highest_flight_count, participating_counts)
        model.add_min_equality(lowest_flight_count, participating_counts)
    else:
        model.add(highest_flight_count == 0)
        model.add(lowest_flight_count == 0)

    flight_count_spread = model.new_int_var(
        0,
        maximum_possible_count,
        "raw_flight_count_spread",
    )
    model.add(
        flight_count_spread
        == highest_flight_count - lowest_flight_count
    )

    pairwise_differences: list[cp_model.IntVar] = []
    for pair_index, (left, right) in enumerate(
        combinations(participating_counts, 2)
    ):
        difference = model.new_int_var(
            0,
            maximum_possible_count,
            f"pairwise_raw_flight_count_difference_{pair_index}",
        )
        model.add_abs_equality(difference, left - right)
        pairwise_differences.append(difference)

    maximum_total_pairwise_difference = (
        len(pairwise_differences) * maximum_possible_count
    )
    total_pairwise_difference = model.new_int_var(
        0,
        maximum_total_pairwise_difference,
        "total_pairwise_raw_flight_count_difference",
    )
    model.add(total_pairwise_difference == sum(pairwise_differences))

    return (
        fairness_employee_indices,
        tuple(fairness_flight_counts),
        highest_flight_count,
        lowest_flight_count,
        flight_count_spread,
        tuple(pairwise_differences),
        total_pairwise_difference,
    )


def _included_employee_indices(
    day: OperationalDay,
    config: OptimizerConfig,
    *,
    include_leads: bool,
) -> tuple[int, ...]:
    """Return enabled employees represented in the selected solve pass."""

    return tuple(
        employee_index
        for employee_index, employee in enumerate(day.employees)
        if employee.enabled
        and any(
            shift.employee_id.strip().casefold()
            == employee.employee_id.strip().casefold()
            and role_is_assignment_eligible(
                shift.normalized_role,
                include_leads=include_leads,
                allow_trainees=config.allow_trainees_for_assignments,
                allow_possible_ramp_support=(
                    config.allow_possible_ramp_support_for_assignments
                ),
            )
            for shift in day.employee_shifts
        )
    )


def _add_consecutive_streak_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    config: OptimizerConfig,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    facts: tuple[FlightOperationalFacts, ...],
    fairness_employee_indices: tuple[int, ...],
) -> tuple[
    dict[tuple[int, int, int], cp_model.IntVar],
    dict[tuple[int, int], cp_model.IntVar],
    tuple[cp_model.IntVar | None, ...],
    cp_model.IntVar,
    cp_model.IntVar,
]:
    """Add exact per-shift consecutive-flight streak variables.

    Immediate-predecessor arcs are only created for pairs whose static gap is
    below the reset threshold. Prefix assignment counts make the intervening
    assignment test constant-size, keeping the formulation quadratic in each
    employee shift's possible assignments.
    """

    assignment_values = _assignment_values_by_employee(
        day,
        decisions,
        fixed_employee_indices,
    )
    fairness_employee_set = set(fairness_employee_indices)
    predecessor_arcs: dict[tuple[int, int, int], cp_model.IntVar] = {}
    run_lengths: dict[tuple[int, int], cp_model.IntVar] = {}
    employee_longest_streaks: list[cp_model.IntVar | None] = []
    participating_longest_streaks: list[cp_model.IntVar] = []
    reset_duration = timedelta(minutes=config.consecutive_reset_minutes)
    maximum_possible_streak = len(day.flights)

    for employee_index, employee in enumerate(day.employees):
        if employee_index not in fairness_employee_set:
            employee_longest_streaks.append(None)
            continue

        assignments_by_shift: dict[int, list[int]] = {}
        for flight_index in assignment_values[employee_index]:
            shift_index = _eligible_shift_index_for_assignment(
                employee,
                day,
                config,
                facts[flight_index].work_start,
                facts[flight_index].work_end,
            )
            assignments_by_shift.setdefault(shift_index, []).append(
                flight_index
            )

        employee_run_lengths: list[cp_model.IntVar] = []
        for shift_index, flight_indices in assignments_by_shift.items():
            ordered_flight_indices = sorted(
                flight_indices,
                key=lambda flight_index: (
                    facts[flight_index].work_start,
                    facts[flight_index].work_end,
                    flight_index,
                ),
            )
            group_size = len(ordered_flight_indices)

            for flight_index in ordered_flight_indices:
                run_length = model.new_int_var(
                    0,
                    group_size,
                    f"consecutive_run_e{employee_index}_f{flight_index}",
                )
                run_lengths[(employee_index, flight_index)] = run_length
                employee_run_lengths.append(run_length)

            prefix_counts: list[cp_model.IntVar | int] = [0]
            for position, flight_index in enumerate(
                ordered_flight_indices,
                start=1,
            ):
                prefix_count = model.new_int_var(
                    0,
                    position,
                    (
                        f"streak_prefix_e{employee_index}_s{shift_index}"
                        f"_p{position}"
                    ),
                )
                model.add(
                    prefix_count
                    == prefix_counts[-1]
                    + assignment_values[employee_index][flight_index]
                )
                prefix_counts.append(prefix_count)

            incoming_arcs: dict[
                int, list[tuple[int, cp_model.IntVar]]
            ] = {flight_index: [] for flight_index in ordered_flight_indices}
            for later_position, later_flight_index in enumerate(
                ordered_flight_indices
            ):
                later_fact = facts[later_flight_index]
                for earlier_position in range(later_position):
                    earlier_flight_index = ordered_flight_indices[
                        earlier_position
                    ]
                    earlier_fact = facts[earlier_flight_index]
                    if (
                        later_fact.work_start - earlier_fact.work_end
                        >= reset_duration
                    ):
                        continue

                    intervening_assignment_count = (
                        prefix_counts[later_position]
                        - prefix_counts[earlier_position + 1]
                    )
                    arc = _add_exact_gap_indicator(
                        model,
                        assignment_values[employee_index][
                            earlier_flight_index
                        ],
                        assignment_values[employee_index][later_flight_index],
                        intervening_assignment_count,
                        (
                            f"streak_predecessor_e{employee_index}"
                            f"_f{earlier_flight_index}_f{later_flight_index}"
                        ),
                    )
                    predecessor_arcs[
                        (
                            employee_index,
                            earlier_flight_index,
                            later_flight_index,
                        )
                    ] = arc
                    incoming_arcs[later_flight_index].append(
                        (earlier_flight_index, arc)
                    )

            for flight_index in ordered_flight_indices:
                presence = assignment_values[employee_index][flight_index]
                run_length = run_lengths[(employee_index, flight_index)]
                incoming = incoming_arcs[flight_index]
                starts_streak = model.new_bool_var(
                    f"starts_streak_e{employee_index}_f{flight_index}"
                )
                model.add(
                    starts_streak + sum(arc for _, arc in incoming)
                    == presence
                )
                if not isinstance(presence, int):
                    model.add(run_length == 0).only_enforce_if(
                        presence.Not()
                    )
                model.add(run_length == 1).only_enforce_if(starts_streak)
                for earlier_flight_index, arc in incoming:
                    model.add(
                        run_length
                        == run_lengths[
                            (employee_index, earlier_flight_index)
                        ]
                        + 1
                    ).only_enforce_if(arc)

        longest_streak = model.new_int_var(
            0,
            maximum_possible_streak,
            f"longest_consecutive_streak_e{employee_index}",
        )
        if employee_run_lengths:
            model.add_max_equality(longest_streak, employee_run_lengths)
        else:
            model.add(longest_streak == 0)
        employee_longest_streaks.append(longest_streak)
        participating_longest_streaks.append(longest_streak)

    maximum_consecutive_flight_streak = model.new_int_var(
        0,
        maximum_possible_streak,
        "maximum_consecutive_flight_streak",
    )
    if participating_longest_streaks:
        model.add_max_equality(
            maximum_consecutive_flight_streak,
            participating_longest_streaks,
        )
    else:
        model.add(maximum_consecutive_flight_streak == 0)

    total_employee_longest_streaks = model.new_int_var(
        0,
        len(participating_longest_streaks) * maximum_possible_streak,
        "total_employee_longest_streaks",
    )
    model.add(
        total_employee_longest_streaks
        == sum(participating_longest_streaks)
    )

    return (
        predecessor_arcs,
        run_lengths,
        tuple(employee_longest_streaks),
        maximum_consecutive_flight_streak,
        total_employee_longest_streaks,
    )


def _add_shift_length_adjustment_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    config: OptimizerConfig,
    fairness_employee_indices: tuple[int, ...],
    fairness_flight_counts: tuple[cp_model.IntVar | None, ...],
) -> tuple[
    tuple[int | None, ...],
    int,
    cp_model.IntVar,
    tuple[cp_model.IntVar | None, ...],
    cp_model.IntVar,
]:
    """Add exact integer deviations from proportional shift-length targets."""

    fairness_employee_set = set(fairness_employee_indices)
    fairness_shift_minutes: list[int | None] = []
    for employee_index in range(len(day.employees)):
        if employee_index not in fairness_employee_set:
            fairness_shift_minutes.append(None)
            continue
        shift_minutes = _scheduled_shift_minutes_for_employee(
            day,
            config,
            employee_index,
        )
        assert shift_minutes > 0
        fairness_shift_minutes.append(shift_minutes)

    total_fairness_shift_minutes = sum(
        shift_minutes
        for shift_minutes in fairness_shift_minutes
        if shift_minutes is not None
    )
    maximum_flight_count = len(day.flights)
    maximum_total_assignment_count = (
        len(fairness_employee_indices) * maximum_flight_count
    )
    total_fairness_assignment_count = model.new_int_var(
        0,
        maximum_total_assignment_count,
        "total_fairness_assignment_count",
    )
    participating_flight_counts = [
        fairness_flight_counts[employee_index]
        for employee_index in fairness_employee_indices
    ]
    assert all(count is not None for count in participating_flight_counts)
    model.add(
        total_fairness_assignment_count
        == sum(
            count for count in participating_flight_counts if count is not None
        )
    )

    shift_adjusted_deviations: list[cp_model.IntVar | None] = []
    participating_deviations: list[cp_model.IntVar] = []
    maximum_total_deviation = 0
    for employee_index, shift_minutes in enumerate(fairness_shift_minutes):
        if shift_minutes is None:
            shift_adjusted_deviations.append(None)
            continue
        flight_count = fairness_flight_counts[employee_index]
        assert flight_count is not None
        maximum_deviation = (
            maximum_flight_count * total_fairness_shift_minutes
            + maximum_total_assignment_count * shift_minutes
        )
        deviation = model.new_int_var(
            0,
            maximum_deviation,
            f"shift_adjusted_flight_count_deviation_e{employee_index}",
        )
        model.add_abs_equality(
            deviation,
            flight_count * total_fairness_shift_minutes
            - total_fairness_assignment_count * shift_minutes,
        )
        shift_adjusted_deviations.append(deviation)
        participating_deviations.append(deviation)
        maximum_total_deviation += maximum_deviation

    total_shift_adjusted_deviation = model.new_int_var(
        0,
        maximum_total_deviation,
        "total_shift_adjusted_flight_count_deviation",
    )
    model.add(
        total_shift_adjusted_deviation == sum(participating_deviations)
    )
    return (
        tuple(fairness_shift_minutes),
        total_fairness_shift_minutes,
        total_fairness_assignment_count,
        tuple(shift_adjusted_deviations),
        total_shift_adjusted_deviation,
    )


def _add_adjusted_workload_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    config: OptimizerConfig,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    staff_counts: tuple[cp_model.IntVar, ...],
    facts: tuple[FlightOperationalFacts, ...],
    fairness_employee_indices: tuple[int, ...],
) -> tuple[
    tuple[cp_model.IntVar, ...],
    dict[tuple[int, int], cp_model.IntVar],
    tuple[cp_model.IntVar | None, ...],
    cp_model.IntVar,
    cp_model.IntVar,
    cp_model.IntVar,
    tuple[cp_model.IntVar, ...],
    cp_model.IntVar,
]:
    """Add exact fixed-point workload values and fairness comparisons."""

    express_factor_units, three_person_factor_units = scaled_workload_factors(
        config
    )
    scale = config.workload_scale
    base_units_by_flight = tuple(
        express_factor_units * scale if flight_facts.express else scale * scale
        for flight_facts in facts
    )
    three_person_units_by_flight = tuple(
        (
            express_factor_units * three_person_factor_units
            if flight_facts.express
            else scale * three_person_factor_units
        )
        for flight_facts in facts
    )

    three_person_staffing = tuple(
        _add_exact_value_indicator(
            model,
            staff_count,
            3,
            f"three_person_staffing_f{flight_index}",
        )
        for flight_index, staff_count in enumerate(staff_counts)
    )
    assigned_to_three_person_flight: dict[
        tuple[int, int], cp_model.IntVar
    ] = {}
    for (employee_index, flight_index), decision in decisions.items():
        assigned_to_three_person_flight[(employee_index, flight_index)] = (
            _add_exact_and_indicator(
                model,
                decision,
                three_person_staffing[flight_index],
                f"assigned_to_three_person_e{employee_index}_f{flight_index}",
            )
        )

    maximum_assignment_units = max(three_person_units_by_flight, default=0)
    if base_units_by_flight:
        maximum_assignment_units = max(
            maximum_assignment_units,
            max(base_units_by_flight),
        )
    maximum_employee_workload = len(day.flights) * maximum_assignment_units
    fairness_employee_set = set(fairness_employee_indices)
    employee_workloads: list[cp_model.IntVar | None] = []
    participating_workloads: list[cp_model.IntVar] = []
    for employee_index in range(len(day.employees)):
        if employee_index not in fairness_employee_set:
            employee_workloads.append(None)
            continue

        contributions: list[cp_model.LinearExpr | int] = []
        for flight_index in range(len(day.flights)):
            base_units = base_units_by_flight[flight_index]
            three_person_increment = (
                three_person_units_by_flight[flight_index] - base_units
            )
            if employee_index in fixed_employee_indices[flight_index]:
                contributions.append(
                    base_units
                    + three_person_increment
                    * three_person_staffing[flight_index]
                )
                continue
            decision = decisions.get((employee_index, flight_index))
            if decision is None:
                continue
            assigned_to_three = assigned_to_three_person_flight[
                (employee_index, flight_index)
            ]
            contributions.append(
                base_units * decision
                + three_person_increment * assigned_to_three
            )

        employee_workload = model.new_int_var(
            0,
            maximum_employee_workload,
            f"adjusted_workload_units_e{employee_index}",
        )
        model.add(employee_workload == sum(contributions))
        employee_workloads.append(employee_workload)
        participating_workloads.append(employee_workload)

    highest_workload = model.new_int_var(
        0,
        maximum_employee_workload,
        "highest_adjusted_workload_units",
    )
    lowest_workload = model.new_int_var(
        0,
        maximum_employee_workload,
        "lowest_adjusted_workload_units",
    )
    if participating_workloads:
        model.add_max_equality(highest_workload, participating_workloads)
        model.add_min_equality(lowest_workload, participating_workloads)
    else:
        model.add(highest_workload == 0)
        model.add(lowest_workload == 0)

    workload_spread = model.new_int_var(
        0,
        maximum_employee_workload,
        "adjusted_workload_spread_units",
    )
    model.add(workload_spread == highest_workload - lowest_workload)

    pairwise_differences: list[cp_model.IntVar] = []
    for pair_index, (left, right) in enumerate(
        combinations(participating_workloads, 2)
    ):
        difference = model.new_int_var(
            0,
            maximum_employee_workload,
            f"pairwise_adjusted_workload_difference_units_{pair_index}",
        )
        model.add_abs_equality(difference, left - right)
        pairwise_differences.append(difference)

    maximum_total_pairwise_difference = (
        len(pairwise_differences) * maximum_employee_workload
    )
    total_pairwise_difference = model.new_int_var(
        0,
        maximum_total_pairwise_difference,
        "total_pairwise_adjusted_workload_difference_units",
    )
    model.add(total_pairwise_difference == sum(pairwise_differences))
    return (
        three_person_staffing,
        assigned_to_three_person_flight,
        tuple(employee_workloads),
        highest_workload,
        lowest_workload,
        workload_spread,
        tuple(pairwise_differences),
        total_pairwise_difference,
    )


def _continuity_eligible_flight_pairs(
    facts: tuple[FlightOperationalFacts, ...],
    config: OptimizerConfig,
) -> tuple[tuple[int, int], ...]:
    """Return all stable non-overlapping flight pairs within the horizon."""

    horizon = timedelta(minutes=config.continuity_horizon_minutes)
    ordered_flight_indices = sorted(
        range(len(facts)),
        key=lambda flight_index: (
            facts[flight_index].work_start,
            facts[flight_index].work_end,
            flight_index,
        ),
    )
    pairs: list[tuple[int, int]] = []
    for position, earlier_flight_index in enumerate(ordered_flight_indices):
        earlier = facts[earlier_flight_index]
        for later_flight_index in ordered_flight_indices[position + 1 :]:
            later = facts[later_flight_index]
            if later.work_start < earlier.work_end:
                continue
            if later.work_start - earlier.work_end > horizon:
                break
            pairs.append((earlier_flight_index, later_flight_index))
    return tuple(pairs)


def _pair_uses_lead_shift(
    day: OperationalDay,
    facts: tuple[FlightOperationalFacts, ...],
    employee_index: int,
    flight_index: int,
) -> bool:
    """Classify a candidate by the containing shift, supporting mixed-role days."""

    employee_id = day.employees[employee_index].employee_id.strip().casefold()
    fact = facts[flight_index]
    return any(
        shift.employee_id.strip().casefold() == employee_id
        and shift.normalized_role is OperationalRole.RAMP_LEAD
        and shift.start <= fact.work_start
        and fact.work_end <= shift.end
        for shift in day.employee_shifts
    )


def _employee_has_lead_shift(day: OperationalDay, employee_index: int) -> bool:
    employee_id = day.employees[employee_index].employee_id.strip().casefold()
    return any(
        shift.employee_id.strip().casefold() == employee_id
        and shift.normalized_role is OperationalRole.RAMP_LEAD
        for shift in day.employee_shifts
    )


def _filter_emergency_candidates(
    day: OperationalDay,
    config: OptimizerConfig,
    candidates: tuple[CandidateAssignment, ...],
    critical_needs: tuple[_CriticalFlightNeed, ...],
) -> tuple[CandidateAssignment, ...]:
    """Admit Leads only on Pass-1-critical flights where they can help."""

    facts = tuple(
        derive_flight_operational_facts(flight, config) for flight in day.flights
    )
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    needs_by_flight = {need.flight_index: need for need in critical_needs}
    filtered: list[CandidateAssignment] = []
    for candidate in candidates:
        employee_index = employee_indices[candidate.employee_id.strip().casefold()]
        flight_index = day.flights.index(candidate.flight)
        if not _pair_uses_lead_shift(day, facts, employee_index, flight_index):
            filtered.append(candidate)
            continue
        need = needs_by_flight.get(flight_index)
        if need is None:
            continue
        qualifications = day.employees[employee_index].qualifications
        if (
            need.below_minimum
            or need.missing_push and Qualification.PUSH in qualifications
            or need.missing_close and Qualification.CLOSE_OUT in qualifications
        ):
            filtered.append(candidate)
    return tuple(filtered)


def _constrain_emergency_lead_value(
    model: cp_model.CpModel,
    day: OperationalDay,
    lead_decisions: dict[tuple[int, int], cp_model.IntVar],
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    staff_counts: tuple[cp_model.IntVar, ...],
    minimum_met: tuple[cp_model.IntVar, ...],
    facts: tuple[FlightOperationalFacts, ...],
    requirements: tuple[StaffingRequirements, ...],
) -> None:
    """Require every selected Lead to have independently measurable critical value."""

    for (employee_index, flight_index), lead_decision in lead_decisions.items():
        above_minimum = _add_exact_threshold_indicator(
            model,
            staff_counts[flight_index],
            requirements[flight_index].minimum + 1,
            f"above_minimum_for_lead_e{employee_index}_f{flight_index}",
        )
        staffing_reason = _add_exact_and_indicator(
            model,
            lead_decision,
            above_minimum.Not(),
            f"lead_minimum_value_e{employee_index}_f{flight_index}",
        )
        reasons: list[cp_model.IntVar] = [staffing_reason]

        if facts[flight_index].flight_type is not FlightType.ARRIVAL_ONLY:
            for qualification, suffix in (
                (Qualification.PUSH, "push"),
                (Qualification.CLOSE_OUT, "close"),
            ):
                if qualification not in day.employees[employee_index].qualifications:
                    continue
                fixed_other_count = sum(
                    other_employee_index != employee_index
                    and qualification
                    in day.employees[other_employee_index].qualifications
                    for other_employee_index in fixed_employee_indices[flight_index]
                )
                other_qualified_decisions = [
                    decision
                    for (
                        other_employee_index,
                        candidate_flight_index,
                    ), decision in decisions.items()
                    if candidate_flight_index == flight_index
                    and other_employee_index != employee_index
                    and qualification
                    in day.employees[other_employee_index].qualifications
                ]
                no_other_qualified = _add_exact_zero_indicator(
                    model,
                    fixed_other_count + sum(other_qualified_decisions),
                    (
                        f"no_other_{suffix}_qualified_for_lead_"
                        f"e{employee_index}_f{flight_index}"
                    ),
                )
                assigned_and_minimum = _add_exact_and_indicator(
                    model,
                    lead_decision,
                    minimum_met[flight_index],
                    (
                        f"lead_assigned_and_minimum_{suffix}_"
                        f"e{employee_index}_f{flight_index}"
                    ),
                )
                reasons.append(
                    _add_exact_and_indicator(
                        model,
                        assigned_and_minimum,
                        no_other_qualified,
                        f"lead_{suffix}_value_e{employee_index}_f{flight_index}",
                    )
                )

        model.add(lead_decision <= sum(reasons))


def _add_continuity_model(
    model: cp_model.CpModel,
    day: OperationalDay,
    config: OptimizerConfig,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[tuple[int, ...], ...],
    facts: tuple[FlightOperationalFacts, ...],
    continuity_employee_indices: tuple[int, ...] | None = None,
) -> tuple[
    tuple[tuple[int, int], ...],
    dict[tuple[int, int, int], cp_model.IntVar],
    cp_model.IntVar,
]:
    """Add exact employee-retention indicators for plausible flight pairs."""

    flight_pairs = _continuity_eligible_flight_pairs(facts, config)
    assignment_values = _assignment_values_by_employee(
        day,
        decisions,
        fixed_employee_indices,
    )
    retained_employee_transitions: dict[
        tuple[int, int, int], cp_model.IntVar
    ] = {}
    included_employee_indices = (
        range(len(day.employees))
        if continuity_employee_indices is None
        else continuity_employee_indices
    )
    for earlier_flight_index, later_flight_index in flight_pairs:
        for employee_index in included_employee_indices:
            employee_assignments = assignment_values[employee_index]
            earlier_assigned = employee_assignments.get(earlier_flight_index)
            later_assigned = employee_assignments.get(later_flight_index)
            if earlier_assigned is None or later_assigned is None:
                continue
            retained_employee_transitions[
                (
                    employee_index,
                    earlier_flight_index,
                    later_flight_index,
                )
            ] = _add_exact_and_indicator(
                model,
                earlier_assigned,
                later_assigned,
                (
                    f"continuity_retained_e{employee_index}"
                    f"_f{earlier_flight_index}_f{later_flight_index}"
                ),
            )

    total_continuity_retention = model.new_int_var(
        0,
        len(retained_employee_transitions),
        "total_continuity_retention",
    )
    model.add(
        total_continuity_retention
        == sum(retained_employee_transitions.values())
    )
    return (
        flight_pairs,
        retained_employee_transitions,
        total_continuity_retention,
    )


def _scheduled_shift_minutes_for_employee(
    day: OperationalDay,
    config: OptimizerConfig,
    employee_index: int,
    *,
    include_leads: bool = False,
) -> int:
    """Sum exact whole minutes for shifts allowed in the ordinary pass."""

    employee_id = day.employees[employee_index].employee_id.strip().casefold()
    one_minute = timedelta(minutes=1)
    total_minutes = 0
    for shift in day.employee_shifts:
        if shift.employee_id.strip().casefold() != employee_id:
            continue
        if not role_is_assignment_eligible(
            shift.normalized_role,
            include_leads=include_leads,
            allow_trainees=config.allow_trainees_for_assignments,
            allow_possible_ramp_support=(
                config.allow_possible_ramp_support_for_assignments
            ),
        ):
            continue
        duration = shift.end - shift.start
        assert duration > timedelta(0)
        assert duration % one_minute == timedelta(0)
        total_minutes += duration // one_minute
    return total_minutes


def _one_eligible_shift_contains_assignment_span(
    employee: Employee,
    day: OperationalDay,
    config: OptimizerConfig,
    span_start: datetime,
    span_end: datetime,
    *,
    include_leads: bool = False,
) -> bool:
    return bool(
        eligible_shifts_for_interval(
            employee,
            day.employee_shifts,
            span_start,
            span_end,
            include_leads=include_leads,
            allow_trainees=config.allow_trainees_for_assignments,
            allow_possible_ramp_support=(
                config.allow_possible_ramp_support_for_assignments
            ),
        )
    )


def _eligible_shift_index_for_assignment(
    employee: Employee,
    day: OperationalDay,
    config: OptimizerConfig,
    span_start: datetime,
    span_end: datetime,
    *,
    include_leads: bool = False,
) -> int:
    """Return the unique eligible shift containing an assignment window."""

    eligible_shifts = eligible_shifts_for_interval(
        employee,
        day.employee_shifts,
        span_start,
        span_end,
        include_leads=include_leads,
        allow_trainees=config.allow_trainees_for_assignments,
        allow_possible_ramp_support=(
            config.allow_possible_ramp_support_for_assignments
        ),
    )
    assert len(eligible_shifts) == 1
    return day.employee_shifts.index(eligible_shifts[0])


def _add_exact_gap_indicator(
    model: cp_model.CpModel,
    earlier_assigned: cp_model.IntVar | int,
    later_assigned: cp_model.IntVar | int,
    intervening_assignment_count: cp_model.LinearExpr | int,
    name: str,
) -> cp_model.IntVar:
    """Link a gap to assigned bounds and the absence of intervening work."""

    indicator = model.new_bool_var(name)
    model.add(indicator <= earlier_assigned)
    model.add(indicator <= later_assigned)
    model.add(intervening_assignment_count == 0).only_enforce_if(indicator)
    model.add(
        indicator
        >= earlier_assigned
        + later_assigned
        - 1
        - intervening_assignment_count
    )
    return indicator


def _add_exact_any_indicator(
    model: cp_model.CpModel,
    values: tuple[cp_model.IntVar, ...],
    name: str,
) -> cp_model.IntVar:
    indicator = model.new_bool_var(name)
    if values:
        model.add(sum(values) >= 1).only_enforce_if(indicator)
        model.add(sum(values) == 0).only_enforce_if(indicator.Not())
    else:
        model.add(indicator == 0)
    return indicator


def _add_exact_threshold_indicator(
    model: cp_model.CpModel,
    staff_count: cp_model.IntVar,
    threshold: int,
    name: str,
) -> cp_model.IntVar:
    indicator = model.new_bool_var(name)
    model.add(staff_count >= threshold).only_enforce_if(indicator)
    model.add(staff_count <= threshold - 1).only_enforce_if(indicator.Not())
    return indicator


def _add_exact_value_indicator(
    model: cp_model.CpModel,
    value: cp_model.IntVar,
    target: int,
    name: str,
) -> cp_model.IntVar:
    """Return a Boolean equal to whether an integer variable equals a value."""

    indicator = model.new_bool_var(name)
    model.add(value == target).only_enforce_if(indicator)
    model.add(value != target).only_enforce_if(indicator.Not())
    return indicator


def _add_exact_zero_indicator(
    model: cp_model.CpModel,
    expression: cp_model.LinearExpr | int,
    name: str,
) -> cp_model.IntVar:
    """Return a Boolean equal to whether a nonnegative expression is zero."""

    indicator = model.new_bool_var(name)
    model.add(expression == 0).only_enforce_if(indicator)
    model.add(expression >= 1).only_enforce_if(indicator.Not())
    return indicator


def _add_exact_qualification_indicator(
    model: cp_model.CpModel,
    day: OperationalDay,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    fixed_employee_indices: tuple[int, ...],
    flight_index: int,
    qualification: Qualification,
    name: str,
) -> cp_model.IntVar:
    """Link coverage exactly to qualified members of the assigned crew."""

    fixed_qualified_count = sum(
        qualification in day.employees[index].qualifications
        for index in fixed_employee_indices
    )
    qualified_decisions = [
        decision
        for (employee_index, candidate_flight_index), decision in decisions.items()
        if candidate_flight_index == flight_index
        and qualification in day.employees[employee_index].qualifications
    ]
    qualified_assigned_count = fixed_qualified_count + sum(qualified_decisions)
    indicator = model.new_bool_var(name)
    model.add(qualified_assigned_count >= 1).only_enforce_if(indicator)
    model.add(qualified_assigned_count == 0).only_enforce_if(indicator.Not())
    return indicator


def _add_exact_and_indicator(
    model: cp_model.CpModel,
    left: cp_model.IntVar | int,
    right: cp_model.IntVar | int,
    name: str,
) -> cp_model.IntVar:
    """Return a Boolean equal to the conjunction of two Boolean variables."""

    indicator = model.new_bool_var(name)
    model.add(indicator <= left)
    model.add(indicator <= right)
    model.add(indicator >= left + right - 1)
    return indicator


def _add_exact_below_minimum_coverage_indicator(
    model: cp_model.CpModel,
    minimum_met: cp_model.IntVar,
    coverage: cp_model.IntVar,
    name: str,
) -> cp_model.IntVar:
    """Return a Boolean equal to coverage AND NOT minimum staffing."""

    indicator = model.new_bool_var(name)
    model.add(indicator <= coverage)
    model.add(indicator + minimum_met <= 1)
    model.add(indicator >= coverage - minimum_met)
    return indicator


def _objective_stages(model_data: _ModelData) -> tuple[_ObjectiveStage, ...]:
    stages = (
        _ObjectiveStage(
            "minimum_covered_flights", True, sum(model_data.minimum_met)
        ),
        _ObjectiveStage(
            "minimum_staffed_qualification_compliant_flights",
            True,
            sum(model_data.minimum_staffed_qualification_compliant),
        ),
        _ObjectiveStage(
            "minimum_staffed_individual_qualification_coverage",
            True,
            sum(model_data.minimum_staffed_qualification_coverage),
        ),
        _ObjectiveStage(
            "total_minimum_shortfall",
            False,
            sum(model_data.minimum_shortfalls),
        ),
        _ObjectiveStage(
            "largest_minimum_shortfall",
            False,
            model_data.largest_minimum_shortfall,
        ),
        # A single exact known-violation stage avoids rewarding extra flights
        # merely to turn a non-evaluable employee into a satisfied one. Among
        # employees who remain evaluable, minimizing violations is equivalent
        # to maximizing achieved breaks.
        _ObjectiveStage(
            "known_unsatisfied_required_breaks",
            False,
            sum(
                indicator
                for indicator in model_data.known_unsatisfied_break
                if indicator is not None
            ),
        ),
        _ObjectiveStage(
            "preferred_staffed_flights", True, sum(model_data.preferred_met)
        ),
        _ObjectiveStage(
            "total_preferred_shortfall",
            False,
            sum(model_data.preferred_shortfalls),
        ),
        _ObjectiveStage(
            "partial_crew_individual_qualification_coverage",
            True,
            sum(model_data.partial_crew_qualification_coverage),
        ),
        _ObjectiveStage(
            "raw_flight_count_spread",
            False,
            model_data.flight_count_spread,
        ),
        _ObjectiveStage(
            "total_pairwise_flight_count_difference",
            False,
            model_data.total_pairwise_flight_count_difference,
        ),
        _ObjectiveStage(
            "maximum_consecutive_flight_streak",
            False,
            model_data.maximum_consecutive_flight_streak,
        ),
        _ObjectiveStage(
            "total_employee_longest_streaks",
            False,
            model_data.total_employee_longest_streaks,
        ),
        _ObjectiveStage(
            "total_shift_adjusted_flight_count_deviation",
            False,
            model_data.total_shift_adjusted_deviation,
        ),
        _ObjectiveStage(
            "adjusted_workload_spread",
            False,
            model_data.adjusted_workload_spread,
        ),
        _ObjectiveStage(
            "total_pairwise_adjusted_workload_difference",
            False,
            model_data.total_pairwise_adjusted_workload_difference,
        ),
        _ObjectiveStage(
            "total_continuity_retention",
            True,
            model_data.total_continuity_retention,
        ),
    )
    if not model_data.include_leads:
        return stages
    return (
        stages[:6]
        + (
            _ObjectiveStage(
                "total_emergency_lead_assignments",
                False,
                model_data.total_lead_assignments,
            ),
        )
        + stages[6:]
    )


def _solve_lexicographically(
    model_data: _ModelData,
    config: OptimizerConfig,
    started_at: float,
) -> tuple[
    OptimizationStatus,
    cp_model.CpSolver | None,
    tuple[ObjectiveValue, ...],
]:
    recorded: list[ObjectiveValue] = []
    last_solver: cp_model.CpSolver | None = None

    for stage_number, stage in enumerate(_objective_stages(model_data), start=1):
        remaining_seconds = config.solver_time_limit_seconds - (
            monotonic() - started_at
        )
        if remaining_seconds <= 0:
            if last_solver is None:
                return OptimizationStatus.UNKNOWN, None, tuple(recorded)
            recorded.append(
                ObjectiveValue(
                    stage=stage_number,
                    name=stage.name,
                    value=_expression_value(last_solver, stage.expression),
                    proven_optimal=False,
                )
            )
            return OptimizationStatus.FEASIBLE, last_solver, tuple(recorded)

        if stage.maximize:
            model_data.model.maximize(stage.expression)
        else:
            model_data.model.minimize(stage.expression)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining_seconds
        solver.parameters.random_seed = config.solver_random_seed
        solver.parameters.num_search_workers = config.solver_num_search_workers
        ortools_status = solver.solve(model_data.model)
        status = _map_status(ortools_status)

        if status is OptimizationStatus.OPTIMAL:
            optimum = _expression_value(solver, stage.expression)
            recorded.append(
                ObjectiveValue(stage_number, stage.name, optimum, True)
            )
            model_data.model.add(stage.expression == optimum)
            last_solver = solver
            continue
        if status is OptimizationStatus.FEASIBLE:
            recorded.append(
                ObjectiveValue(
                    stage_number,
                    stage.name,
                    _expression_value(solver, stage.expression),
                    False,
                )
            )
            return status, solver, tuple(recorded)
        if status is OptimizationStatus.UNKNOWN and last_solver is not None:
            recorded.append(
                ObjectiveValue(
                    stage_number,
                    stage.name,
                    _expression_value(last_solver, stage.expression),
                    False,
                )
            )
            return OptimizationStatus.FEASIBLE, last_solver, tuple(recorded)
        return status, None, tuple(recorded)

    return OptimizationStatus.OPTIMAL, last_solver, tuple(recorded)


def _expression_value(
    solver: cp_model.CpSolver, expression: cp_model.LinearExpr | int
) -> int:
    if isinstance(expression, int):
        return expression
    return int(solver.value(expression))


def _map_status(status: cp_model.CpSolverStatus) -> OptimizationStatus:
    if status == cp_model.OPTIMAL:
        return OptimizationStatus.OPTIMAL
    if status == cp_model.FEASIBLE:
        return OptimizationStatus.FEASIBLE
    if status == cp_model.INFEASIBLE:
        return OptimizationStatus.INFEASIBLE
    return OptimizationStatus.UNKNOWN


def _assigned_employee_indices_by_flight(
    model_data: _ModelData,
    solver: cp_model.CpSolver,
) -> tuple[tuple[int, ...], ...]:
    assigned_by_flight = [
        set(employee_indices)
        for employee_indices in model_data.fixed_employee_indices
    ]
    for (employee_index, flight_index), decision in model_data.decisions.items():
        if solver.value(decision):
            assigned_by_flight[flight_index].add(employee_index)
    return tuple(tuple(sorted(indices)) for indices in assigned_by_flight)


def _derive_continuity_metrics(
    day: OperationalDay,
    model_data: _ModelData,
    solver: cp_model.CpSolver,
    assigned_by_flight: tuple[tuple[int, ...], ...],
) -> ContinuityMetrics:
    """Reconstruct continuity from final crews and verify model indicators."""

    modeled_by_pair: dict[
        tuple[int, int], list[tuple[int, cp_model.IntVar]]
    ] = {}
    for (
        employee_index,
        earlier_flight_index,
        later_flight_index,
    ), retained in model_data.retained_employee_transitions.items():
        modeled_by_pair.setdefault(
            (earlier_flight_index, later_flight_index),
            [],
        ).append((employee_index, retained))

    transitions: list[ContinuityTransitionResult] = []
    continuity_employee_set = set(model_data.fairness_employee_indices)
    for earlier_flight_index, later_flight_index in (
        model_data.continuity_flight_pairs
    ):
        earlier_assigned = set(assigned_by_flight[earlier_flight_index])
        later_assigned = set(assigned_by_flight[later_flight_index])
        retained_employee_indices = tuple(
            employee_index
            for employee_index in range(len(day.employees))
            if employee_index in earlier_assigned
            and employee_index in later_assigned
            and employee_index in continuity_employee_set
        )
        modeled_retained_employee_indices = tuple(
            employee_index
            for employee_index, retained in modeled_by_pair.get(
                (earlier_flight_index, later_flight_index),
                (),
            )
            if solver.value(retained)
        )
        assert modeled_retained_employee_indices == retained_employee_indices

        retained_employee_ids = tuple(
            day.employees[employee_index].employee_id
            for employee_index in retained_employee_indices
        )
        transitions.append(
            ContinuityTransitionResult(
                previous_flight=day.flights[earlier_flight_index],
                next_flight=day.flights[later_flight_index],
                retained_employee_ids=retained_employee_ids,
                retention_count=len(retained_employee_ids),
            )
        )

    total_retained_employee_transitions = sum(
        transition.retention_count for transition in transitions
    )
    assert solver.value(model_data.total_continuity_retention) == (
        total_retained_employee_transitions
    )
    strongest_transition = max(
        transitions,
        key=lambda transition: transition.retention_count,
        default=None,
    )
    eligible_transition_count = len(transitions)
    return ContinuityMetrics(
        eligible_transition_count=eligible_transition_count,
        total_retained_employee_transitions=(
            total_retained_employee_transitions
        ),
        average_retained_employees_per_transition=(
            total_retained_employee_transitions / eligible_transition_count
            if eligible_transition_count
            else 0.0
        ),
        strongest_retention_count=(
            strongest_transition.retention_count
            if strongest_transition is not None
            else 0
        ),
        strongest_transition=strongest_transition,
        transitions=tuple(transitions),
    )


def _build_result(
    day: OperationalDay,
    config: OptimizerConfig,
    model_data: _ModelData,
    solver: cp_model.CpSolver,
    status: OptimizationStatus,
    objectives: tuple[ObjectiveValue, ...],
    runtime: float,
) -> OptimizationResult:
    flight_results: list[FlightAssignmentResult] = []
    all_warnings: list[ScheduleWarning] = []
    assigned_by_flight = _assigned_employee_indices_by_flight(model_data, solver)

    for flight_index, flight in enumerate(day.flights):
        assigned_indices = assigned_by_flight[flight_index]

        assigned_employee_ids = tuple(
            employee.employee_id
            for employee_index, employee in enumerate(day.employees)
            if employee_index in assigned_indices
        )
        fixed_indices = set(model_data.fixed_employee_indices[flight_index])
        fixed_employee_ids = tuple(
            employee.employee_id
            for employee_index, employee in enumerate(day.employees)
            if employee_index in fixed_indices
        )
        requirements = model_data.requirements[flight_index]
        facts = model_data.facts[flight_index]
        staffing_count = len(assigned_employee_ids)
        assert bool(solver.value(model_data.three_person_staffing[flight_index])) is (
            staffing_count == 3
        )
        minimum_met = staffing_count >= requirements.minimum
        preferred_met = staffing_count >= requirements.preferred
        minimum_shortfall = max(0, requirements.minimum - staffing_count)
        preferred_shortfall = max(0, requirements.preferred - staffing_count)
        staffing_status = (
            StaffingStatus.PREFERRED_STAFFED
            if preferred_met
            else StaffingStatus.MINIMUM_STAFFED
            if minimum_met
            else StaffingStatus.BELOW_MINIMUM
        )

        flight_warnings: list[ScheduleWarning] = []
        if not minimum_met:
            warning = ScheduleWarning(
                code=WarningCode.MINIMUM_STAFFING_NOT_MET,
                severity=WarningSeverity.CRITICAL,
                message=(
                    f"Flight is below minimum staffing by {minimum_shortfall}"
                ),
                arrival_flight_number=flight.arrival_flight_number,
                departure_flight_number=flight.departure_flight_number,
            )
            flight_warnings.append(warning)
            all_warnings.append(warning)

        if facts.flight_type is FlightType.ARRIVAL_ONLY:
            push_covered = None
            close_covered = None
        else:
            assigned_employees = (
                day.employees[index] for index in assigned_indices
            )
            assigned_qualifications = frozenset(
                qualification
                for employee in assigned_employees
                for qualification in employee.qualifications
            )
            push_covered = Qualification.PUSH in assigned_qualifications
            close_covered = Qualification.CLOSE_OUT in assigned_qualifications
            if not push_covered:
                warning = ScheduleWarning(
                    code=WarningCode.PUSH_QUALIFICATION_NOT_MET,
                    severity=WarningSeverity.CRITICAL,
                    message="Assigned crew does not include a push-qualified employee",
                    arrival_flight_number=flight.arrival_flight_number,
                    departure_flight_number=flight.departure_flight_number,
                )
                flight_warnings.append(warning)
                all_warnings.append(warning)
            if not close_covered:
                warning = ScheduleWarning(
                    code=WarningCode.CLOSE_QUALIFICATION_NOT_MET,
                    severity=WarningSeverity.CRITICAL,
                    message=(
                        "Assigned crew does not include a close-out-qualified employee"
                    ),
                    arrival_flight_number=flight.arrival_flight_number,
                    departure_flight_number=flight.departure_flight_number,
                )
                flight_warnings.append(warning)
                all_warnings.append(warning)

        flight_results.append(
            FlightAssignmentResult(
                flight=flight,
                flight_type=facts.flight_type,
                work_start=facts.work_start,
                work_end=facts.work_end,
                assigned_employee_ids=assigned_employee_ids,
                fixed_employee_ids=fixed_employee_ids,
                staffing_count=staffing_count,
                minimum_staff=requirements.minimum,
                preferred_staff=requirements.preferred,
                maximum_staff=requirements.maximum,
                staffing_status=staffing_status,
                minimum_met=minimum_met,
                minimum_shortfall=minimum_shortfall,
                preferred_met=preferred_met,
                preferred_shortfall=preferred_shortfall,
                express=facts.express,
                heavy=flight.heavy,
                push_covered=push_covered,
                close_covered=close_covered,
                warnings=tuple(flight_warnings),
            )
        )

    continuity_metrics = _derive_continuity_metrics(
        day,
        model_data,
        solver,
        assigned_by_flight,
    )

    fairness_counts = tuple(
        sum(
            employee_index in assigned_employee_indices
            for assigned_employee_indices in assigned_by_flight
        )
        for employee_index in model_data.fairness_employee_indices
    )
    total_assignments = sum(fairness_counts)
    assert (
        solver.value(model_data.total_fairness_assignment_count)
        == total_assignments
    )

    proportional_targets: dict[int, float] = {}
    public_shift_deviations: dict[int, float] = {}
    scaled_shift_deviations: list[int] = []
    total_shift_minutes = model_data.total_fairness_shift_minutes
    for employee_index, flight_count in zip(
        model_data.fairness_employee_indices,
        fairness_counts,
    ):
        modeled_count = model_data.fairness_flight_counts[employee_index]
        shift_minutes = model_data.fairness_shift_minutes[employee_index]
        modeled_deviation = model_data.shift_adjusted_deviations[employee_index]
        assert modeled_count is not None
        assert shift_minutes is not None
        assert modeled_deviation is not None
        assert solver.value(modeled_count) == flight_count

        target_numerator = total_assignments * shift_minutes
        scaled_deviation = abs(
            flight_count * total_shift_minutes - target_numerator
        )
        assert solver.value(modeled_deviation) == scaled_deviation
        scaled_shift_deviations.append(scaled_deviation)
        proportional_targets[employee_index] = (
            target_numerator / total_shift_minutes
        )
        public_shift_deviations[employee_index] = (
            scaled_deviation / total_shift_minutes
        )

    total_scaled_shift_deviation = sum(scaled_shift_deviations)
    assert (
        solver.value(model_data.total_shift_adjusted_deviation)
        == total_scaled_shift_deviation
    )

    adjusted_units_by_employee: dict[int, int] = {}
    for employee_index in model_data.included_employee_indices:
        adjusted_units = sum(
            adjusted_assignment_workload_units(
                model_data.facts[flight_index],
                flight_results[flight_index].staffing_count,
                config,
            )
            for flight_index, assigned_employee_indices in enumerate(
                assigned_by_flight
            )
            if employee_index in assigned_employee_indices
        )
        adjusted_units_by_employee[employee_index] = adjusted_units
        modeled_workload = model_data.adjusted_workload_units[employee_index]
        if modeled_workload is not None:
            assert solver.value(modeled_workload) == adjusted_units

    fairness_workloads = tuple(
        adjusted_units_by_employee[employee_index]
        for employee_index in model_data.fairness_employee_indices
    )
    highest_adjusted_workload = max(fairness_workloads, default=0)
    lowest_adjusted_workload = min(fairness_workloads, default=0)
    adjusted_workload_spread_units = (
        highest_adjusted_workload - lowest_adjusted_workload
    )
    total_pairwise_adjusted_workload_difference = sum(
        abs(left - right)
        for left, right in combinations(fairness_workloads, 2)
    )
    assert (
        solver.value(model_data.highest_adjusted_workload)
        == highest_adjusted_workload
    )
    assert (
        solver.value(model_data.lowest_adjusted_workload)
        == lowest_adjusted_workload
    )
    assert (
        solver.value(model_data.adjusted_workload_spread)
        == adjusted_workload_spread_units
    )
    assert (
        solver.value(model_data.total_pairwise_adjusted_workload_difference)
        == total_pairwise_adjusted_workload_difference
    )

    modeled_runs_by_employee: dict[
        int, list[tuple[int, cp_model.IntVar]]
    ] = {}
    for (
        employee_index,
        flight_index,
    ), modeled_run_length in model_data.streak_run_lengths.items():
        modeled_runs_by_employee.setdefault(employee_index, []).append(
            (flight_index, modeled_run_length)
        )
    modeled_arcs_by_employee: dict[
        int, list[tuple[int, int, cp_model.IntVar]]
    ] = {}
    for (
        employee_index,
        earlier_flight_index,
        later_flight_index,
    ), modeled_arc in model_data.streak_predecessor_arcs.items():
        modeled_arcs_by_employee.setdefault(employee_index, []).append(
            (earlier_flight_index, later_flight_index, modeled_arc)
        )

    employee_results: list[EmployeeScheduleResult] = []
    longest_streaks_by_employee: dict[int, int] = {}
    for employee_index in model_data.included_employee_indices:
        assigned_flight_indices = tuple(
            flight_index
            for flight_index, assigned_employee_indices in enumerate(
                assigned_by_flight
            )
            if employee_index in assigned_employee_indices
        )
        ordered_flight_indices = tuple(
            sorted(
                assigned_flight_indices,
                key=lambda flight_index: (
                    model_data.facts[flight_index].work_start,
                    model_data.facts[flight_index].work_end,
                    flight_index,
                ),
            )
        )
        (
            longest_consecutive_streak,
            streak_run_lengths,
            streak_predecessors,
        ) = _derive_consecutive_streaks(
            day,
            config,
            model_data.facts,
            employee_index,
            ordered_flight_indices,
            include_leads=model_data.include_leads,
        )
        longest_streaks_by_employee[employee_index] = (
            longest_consecutive_streak
        )
        modeled_longest_streak = model_data.employee_longest_streaks[
            employee_index
        ]
        if modeled_longest_streak is not None:
            assert (
                solver.value(modeled_longest_streak)
                == longest_consecutive_streak
            )
        for flight_index, modeled_run_length in modeled_runs_by_employee.get(
            employee_index,
            (),
        ):
            assert solver.value(modeled_run_length) == (
                streak_run_lengths.get(flight_index, 0)
            )
        for (
            earlier_flight_index,
            later_flight_index,
            modeled_arc,
        ) in modeled_arcs_by_employee.get(employee_index, ()):
            assert bool(solver.value(modeled_arc)) is (
                streak_predecessors.get(later_flight_index)
                == earlier_flight_index
            )
        break_status = _derive_break_status(
            day,
            config,
            model_data.facts,
            employee_index,
            ordered_flight_indices,
            include_leads=model_data.include_leads,
        )
        if (
            not ordered_flight_indices
            and _employee_has_lead_shift(day, employee_index)
        ):
            break_status = BreakStatus.NOT_APPLICABLE
        evaluable = model_data.break_evaluable[employee_index]
        achieved = model_data.break_achieved[employee_index]
        unsatisfied = model_data.known_unsatisfied_break[employee_index]
        assert evaluable is not None
        assert achieved is not None
        assert unsatisfied is not None
        assert bool(solver.value(evaluable)) is (
            break_status
            in {BreakStatus.SATISFIED, BreakStatus.UNSATISFIED}
        )
        assert bool(solver.value(achieved)) is (
            break_status is BreakStatus.SATISFIED
        )
        assert bool(solver.value(unsatisfied)) is (
            break_status is BreakStatus.UNSATISFIED
        )

        if break_status is BreakStatus.UNSATISFIED:
            all_warnings.append(
                ScheduleWarning(
                    code=WarningCode.REQUIRED_BREAK_NOT_MET,
                    severity=WarningSeverity.CRITICAL,
                    message=(
                        "Employee has no uninterrupted between-assignment "
                        f"break of at least {config.required_break_minutes} minutes"
                    ),
                    employee_id=day.employees[employee_index].employee_id,
                )
            )

        mainline_flight_count = sum(
            not model_data.facts[flight_index].express
            for flight_index in ordered_flight_indices
        )
        express_flight_count = sum(
            model_data.facts[flight_index].express
            for flight_index in ordered_flight_indices
        )
        assert mainline_flight_count + express_flight_count == len(
            ordered_flight_indices
        )
        employee_results.append(
            EmployeeScheduleResult(
                employee_id=day.employees[employee_index].employee_id,
                assigned_flights=tuple(
                    day.flights[flight_index]
                    for flight_index in ordered_flight_indices
                ),
                flight_count=len(ordered_flight_indices),
                mainline_flight_count=mainline_flight_count,
                express_flight_count=express_flight_count,
                three_person_flight_count=sum(
                    flight_results[flight_index].staffing_count == 3
                    for flight_index in ordered_flight_indices
                ),
                longest_consecutive_streak=longest_consecutive_streak,
                break_status=break_status,
                adjusted_workload=workload_units_to_public_value(
                    adjusted_units_by_employee[employee_index],
                    config,
                ),
                scheduled_shift_minutes=_scheduled_shift_minutes_for_employee(
                    day,
                    config,
                    employee_index,
                    include_leads=model_data.include_leads,
                ),
                proportional_target_flight_count=proportional_targets.get(
                    employee_index
                ),
                shift_adjusted_deviation=public_shift_deviations.get(
                    employee_index
                ),
            )
        )

    highest_flight_count = max(fairness_counts, default=0)
    lowest_flight_count = min(fairness_counts, default=0)
    flight_count_spread = highest_flight_count - lowest_flight_count
    total_pairwise_difference = sum(
        abs(left - right)
        for left, right in combinations(fairness_counts, 2)
    )
    assert solver.value(model_data.highest_flight_count) == highest_flight_count
    assert solver.value(model_data.lowest_flight_count) == lowest_flight_count
    assert solver.value(model_data.flight_count_spread) == flight_count_spread
    assert (
        solver.value(model_data.total_pairwise_flight_count_difference)
        == total_pairwise_difference
    )
    fairness_streaks = tuple(
        longest_streaks_by_employee[employee_index]
        for employee_index in model_data.fairness_employee_indices
    )
    maximum_consecutive_streak = max(fairness_streaks, default=0)
    assert (
        solver.value(model_data.maximum_consecutive_flight_streak)
        == maximum_consecutive_streak
    )
    assert solver.value(model_data.total_employee_longest_streaks) == sum(
        fairness_streaks
    )
    participating_employee_count = len(fairness_counts)
    fairness_metrics = FairnessMetrics(
        participating_employee_count=participating_employee_count,
        total_assignments=total_assignments,
        average_flights=(
            total_assignments / participating_employee_count
            if participating_employee_count
            else 0.0
        ),
        highest_flight_count=highest_flight_count,
        lowest_flight_count=lowest_flight_count,
        flight_count_spread=flight_count_spread,
        maximum_consecutive_streak=maximum_consecutive_streak,
        adjusted_workload_spread=workload_units_to_public_value(
            adjusted_workload_spread_units,
            config,
        ),
        total_participating_shift_minutes=total_shift_minutes,
        total_shift_adjusted_deviation=(
            total_scaled_shift_deviation / total_shift_minutes
            if total_shift_minutes
            else 0.0
        ),
    )

    assert isfinite(runtime)
    return OptimizationResult(
        status=status,
        flight_results=tuple(flight_results),
        employee_results=tuple(employee_results),
        fairness_metrics=fairness_metrics,
        continuity_metrics=continuity_metrics,
        attempts=(),
        objective_values=objectives,
        warnings=tuple(all_warnings),
        emergency_lead_staffing_used=None,
        solver_runtime_seconds=runtime,
    )


def _derive_break_status(
    day: OperationalDay,
    config: OptimizerConfig,
    facts: tuple[FlightOperationalFacts, ...],
    employee_index: int,
    ordered_flight_indices: tuple[int, ...],
    *,
    include_leads: bool = False,
) -> BreakStatus:
    """Derive public break status from final chronological assignments."""

    if len(ordered_flight_indices) < 2:
        return BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS

    required_duration = timedelta(minutes=config.required_break_minutes)
    employee = day.employees[employee_index]
    for earlier_flight_index, later_flight_index in zip(
        ordered_flight_indices,
        ordered_flight_indices[1:],
    ):
        earlier = facts[earlier_flight_index]
        later = facts[later_flight_index]
        if later.work_start - earlier.work_end < required_duration:
            continue
        if _one_eligible_shift_contains_assignment_span(
            employee,
            day,
            config,
            earlier.work_start,
            later.work_end,
            include_leads=include_leads,
        ):
            return BreakStatus.SATISFIED
    return BreakStatus.UNSATISFIED


def _derive_consecutive_streaks(
    day: OperationalDay,
    config: OptimizerConfig,
    facts: tuple[FlightOperationalFacts, ...],
    employee_index: int,
    assigned_flight_indices: tuple[int, ...],
    *,
    include_leads: bool = False,
) -> tuple[int, dict[int, int], dict[int, int]]:
    """Reconstruct per-flight runs and immediate predecessors by shift."""

    employee = day.employees[employee_index]
    assignments_by_shift: dict[int, list[int]] = {}
    for flight_index in assigned_flight_indices:
        shift_index = _eligible_shift_index_for_assignment(
            employee,
            day,
            config,
            facts[flight_index].work_start,
            facts[flight_index].work_end,
            include_leads=include_leads,
        )
        assignments_by_shift.setdefault(shift_index, []).append(flight_index)

    reset_duration = timedelta(minutes=config.consecutive_reset_minutes)
    run_lengths: dict[int, int] = {}
    predecessors: dict[int, int] = {}
    longest_streak = 0
    for flight_indices in assignments_by_shift.values():
        ordered_flight_indices = sorted(
            flight_indices,
            key=lambda flight_index: (
                facts[flight_index].work_start,
                facts[flight_index].work_end,
                flight_index,
            ),
        )
        previous_flight_index: int | None = None
        current_streak = 0
        for flight_index in ordered_flight_indices:
            if previous_flight_index is None:
                current_streak = 1
            else:
                previous_fact = facts[previous_flight_index]
                current_fact = facts[flight_index]
                if (
                    current_fact.work_start - previous_fact.work_end
                    >= reset_duration
                ):
                    current_streak = 1
                else:
                    current_streak += 1
                    predecessors[flight_index] = previous_flight_index
            run_lengths[flight_index] = current_streak
            longest_streak = max(longest_streak, current_streak)
            previous_flight_index = flight_index

    return longest_streak, run_lengths, predecessors


def _empty_solver_result(
    status: OptimizationStatus,
    objectives: tuple[ObjectiveValue, ...],
    runtime: float,
) -> OptimizationResult:
    return OptimizationResult(
        status=status,
        flight_results=(),
        employee_results=(),
        fairness_metrics=None,
        continuity_metrics=None,
        attempts=(),
        objective_values=objectives,
        warnings=(),
        emergency_lead_staffing_used=None,
        solver_runtime_seconds=runtime,
    )
