"""Pure, explicit mapping between API schemas and Phase 1 domain records."""

from dataclasses import dataclass, fields

from ramp_optimizer import (
    ContinuityMetrics,
    ContinuityTransitionResult,
    EmergencyLeadAssignmentResult,
    Employee,
    EmployeeScheduleResult,
    EmployeeShift,
    FairnessMetrics,
    FixedAssignment,
    Flight,
    FlightAssignmentResult,
    FlightNumberParseError,
    ObjectiveValue,
    OperationalDay,
    OptimizationAttemptSummary,
    OptimizationResult,
    OptimizerConfig,
    ScheduleSummary,
    ScheduleWarning,
    ValidationIssue,
    parse_numeric_flight_number,
)
from ramp_optimizer_api.schemas import (
    ContinuityMetricsResponse,
    ContinuityTransitionResponse,
    EmergencyLeadAssignmentResponse,
    EmployeeRequest,
    EmployeeScheduleResponse,
    EmployeeShiftRequest,
    FairnessMetricsResponse,
    FixedAssignmentRequest,
    FlightAssignmentResponse,
    FlightReferenceRequest,
    FlightRequest,
    FlightResponse,
    ObjectiveValueResponse,
    OperationalDayRequest,
    OptimizationAttemptResponse,
    OptimizationRequest,
    OptimizationResponse,
    OptimizerConfigRequest,
    ScheduleSummaryResponse,
    ScheduleWarningResponse,
    ValidationIssueResponse,
)


@dataclass(frozen=True, slots=True)
class MappedOptimizationRequest:
    """Domain inputs plus API-only reference-resolution issues."""

    operational_day: OperationalDay
    config: OptimizerConfig
    issues: tuple[ValidationIssue, ...] = ()


def employee_to_domain(value: EmployeeRequest) -> Employee:
    return Employee(
        employee_id=value.employee_id,
        name=value.name,
        qualifications=frozenset(value.qualifications),
        enabled=value.enabled,
    )


def employee_to_request(value: Employee) -> EmployeeRequest:
    return EmployeeRequest(
        employee_id=value.employee_id,
        name=value.name,
        qualifications=tuple(sorted(value.qualifications, key=lambda item: item.value)),
        enabled=value.enabled,
    )


def shift_to_domain(value: EmployeeShiftRequest) -> EmployeeShift:
    return EmployeeShift(
        employee_id=value.employee_id,
        start=value.start,
        end=value.end,
        normalized_role=value.normalized_role,
    )


def shift_to_request(value: EmployeeShift) -> EmployeeShiftRequest:
    return EmployeeShiftRequest(
        employee_id=value.employee_id,
        start=value.start,
        end=value.end,
        normalized_role=value.normalized_role,
    )


def flight_to_domain(value: FlightRequest) -> Flight:
    return Flight(
        arrival_flight_number=value.arrival_flight_number,
        arrival_time=value.arrival_time,
        departure_flight_number=value.departure_flight_number,
        departure_time=value.departure_time,
        gate=value.gate,
        heavy=value.heavy,
    )


def flight_to_request(value: Flight) -> FlightRequest:
    return FlightRequest(
        arrival_flight_number=value.arrival_flight_number,
        arrival_time=value.arrival_time,
        departure_flight_number=value.departure_flight_number,
        departure_time=value.departure_time,
        gate=value.gate,
        heavy=value.heavy,
    )


def fixed_assignment_to_request(value: FixedAssignment) -> FixedAssignmentRequest:
    return FixedAssignmentRequest(
        employee_id=value.employee_id,
        flight=FlightReferenceRequest(
            arrival_flight_number=value.flight.arrival_flight_number,
            departure_flight_number=value.flight.departure_flight_number,
        ),
    )


def config_to_domain(value: OptimizerConfigRequest) -> OptimizerConfig:
    names = tuple(item.name for item in fields(OptimizerConfig))
    return OptimizerConfig(**{name: getattr(value, name) for name in names})


def config_to_request(value: OptimizerConfig) -> OptimizerConfigRequest:
    names = tuple(item.name for item in fields(OptimizerConfig))
    return OptimizerConfigRequest(**{name: getattr(value, name) for name in names})


def map_optimization_request(value: OptimizationRequest) -> MappedOptimizationRequest:
    """Map a complete request and resolve each directional flight reference."""

    flights = tuple(flight_to_domain(item) for item in value.operational_day.flights)
    fixed_assignments: list[FixedAssignment] = []
    issues: list[ValidationIssue] = []
    for index, item in enumerate(value.operational_day.fixed_assignments):
        flight, issue = _resolve_flight_reference(item.flight, flights, index)
        if issue is not None:
            issues.append(issue)
        else:
            assert flight is not None
            fixed_assignments.append(FixedAssignment(item.employee_id, flight))

    day = OperationalDay(
        operational_date=value.operational_day.operational_date,
        employees=tuple(employee_to_domain(item) for item in value.operational_day.employees),
        employee_shifts=tuple(
            shift_to_domain(item) for item in value.operational_day.employee_shifts
        ),
        flights=flights,
        fixed_assignments=tuple(fixed_assignments),
    )
    return MappedOptimizationRequest(
        operational_day=day,
        config=config_to_domain(value.config),
        issues=tuple(issues),
    )


def operational_day_to_request(
    day: OperationalDay,
    config: OptimizerConfig | None = None,
) -> OptimizationRequest:
    """Create a deterministic API request from validated domain input."""

    return OptimizationRequest(
        operational_day=OperationalDayRequest(
            operational_date=day.operational_date,
            employees=tuple(employee_to_request(item) for item in day.employees),
            employee_shifts=tuple(shift_to_request(item) for item in day.employee_shifts),
            flights=tuple(flight_to_request(item) for item in day.flights),
            fixed_assignments=tuple(
                fixed_assignment_to_request(item) for item in day.fixed_assignments
            ),
        ),
        config=config_to_request(config or OptimizerConfig()),
    )


def validation_issue_to_response(value: ValidationIssue) -> ValidationIssueResponse:
    return ValidationIssueResponse(code=value.code, path=value.path, message=value.message)


def flight_to_response(value: Flight) -> FlightResponse:
    return FlightResponse(
        arrival_flight_number=value.arrival_flight_number,
        arrival_time=value.arrival_time,
        departure_flight_number=value.departure_flight_number,
        departure_time=value.departure_time,
        gate=value.gate,
        heavy=value.heavy,
    )


def warning_to_response(value: ScheduleWarning) -> ScheduleWarningResponse:
    return ScheduleWarningResponse(
        code=value.code,
        severity=value.severity,
        message=value.message,
        arrival_flight_number=value.arrival_flight_number,
        departure_flight_number=value.departure_flight_number,
        employee_id=value.employee_id,
    )


def flight_result_to_response(value: FlightAssignmentResult) -> FlightAssignmentResponse:
    return FlightAssignmentResponse(
        flight=flight_to_response(value.flight),
        flight_type=value.flight_type,
        work_start=value.work_start,
        work_end=value.work_end,
        assigned_employee_ids=value.assigned_employee_ids,
        fixed_employee_ids=value.fixed_employee_ids,
        staffing_count=value.staffing_count,
        minimum_staff=value.minimum_staff,
        preferred_staff=value.preferred_staff,
        maximum_staff=value.maximum_staff,
        staffing_status=value.staffing_status,
        minimum_met=value.minimum_met,
        minimum_shortfall=value.minimum_shortfall,
        preferred_met=value.preferred_met,
        preferred_shortfall=value.preferred_shortfall,
        express=value.express,
        heavy=value.heavy,
        push_covered=value.push_covered,
        close_covered=value.close_covered,
        warnings=tuple(warning_to_response(item) for item in value.warnings),
    )


def employee_result_to_response(value: EmployeeScheduleResult) -> EmployeeScheduleResponse:
    return EmployeeScheduleResponse(
        employee_id=value.employee_id,
        assigned_flights=tuple(flight_to_response(item) for item in value.assigned_flights),
        flight_count=value.flight_count,
        mainline_flight_count=value.mainline_flight_count,
        express_flight_count=value.express_flight_count,
        three_person_flight_count=value.three_person_flight_count,
        longest_consecutive_streak=value.longest_consecutive_streak,
        break_status=value.break_status,
        adjusted_workload=value.adjusted_workload,
        scheduled_shift_minutes=value.scheduled_shift_minutes,
        proportional_target_flight_count=value.proportional_target_flight_count,
        shift_adjusted_deviation=value.shift_adjusted_deviation,
    )


def fairness_to_response(value: FairnessMetrics) -> FairnessMetricsResponse:
    return FairnessMetricsResponse(
        participating_employee_count=value.participating_employee_count,
        total_assignments=value.total_assignments,
        average_flights=value.average_flights,
        highest_flight_count=value.highest_flight_count,
        lowest_flight_count=value.lowest_flight_count,
        flight_count_spread=value.flight_count_spread,
        maximum_consecutive_streak=value.maximum_consecutive_streak,
        adjusted_workload_spread=value.adjusted_workload_spread,
        total_participating_shift_minutes=value.total_participating_shift_minutes,
        total_shift_adjusted_deviation=value.total_shift_adjusted_deviation,
    )


def continuity_transition_to_response(
    value: ContinuityTransitionResult,
) -> ContinuityTransitionResponse:
    return ContinuityTransitionResponse(
        previous_flight=flight_to_response(value.previous_flight),
        next_flight=flight_to_response(value.next_flight),
        retained_employee_ids=value.retained_employee_ids,
        retention_count=value.retention_count,
    )


def continuity_to_response(value: ContinuityMetrics) -> ContinuityMetricsResponse:
    return ContinuityMetricsResponse(
        eligible_transition_count=value.eligible_transition_count,
        total_retained_employee_transitions=value.total_retained_employee_transitions,
        average_retained_employees_per_transition=(
            value.average_retained_employees_per_transition
        ),
        strongest_retention_count=value.strongest_retention_count,
        strongest_transition=(
            continuity_transition_to_response(value.strongest_transition)
            if value.strongest_transition is not None
            else None
        ),
        transitions=tuple(
            continuity_transition_to_response(item) for item in value.transitions
        ),
    )


def attempt_to_response(value: OptimizationAttemptSummary) -> OptimizationAttemptResponse:
    return OptimizationAttemptResponse(
        included_leads=value.included_leads,
        status=value.status,
        minimum_staffed_flights=value.minimum_staffed_flights,
        qualification_compliant_flights=value.qualification_compliant_flights,
        lead_assignments=value.lead_assignments,
        critical_shortage_count=value.critical_shortage_count,
        lead_candidate_count=value.lead_candidate_count,
        solver_runtime_seconds=value.solver_runtime_seconds,
        pass_number=value.pass_number,
        attempt_label=value.attempt_label,
        usable_schedule=value.usable_schedule,
        selected_as_final=value.selected_as_final,
        known_unsatisfied_required_break_count=(
            value.known_unsatisfied_required_break_count
        ),
        objective_stages_completed=value.objective_stages_completed,
        all_objectives_proven_optimal=value.all_objectives_proven_optimal,
    )


def objective_to_response(value: ObjectiveValue) -> ObjectiveValueResponse:
    return ObjectiveValueResponse(
        stage=value.stage,
        name=value.name,
        value=value.value,
        proven_optimal=value.proven_optimal,
    )


def lead_assignment_to_response(
    value: EmergencyLeadAssignmentResult,
) -> EmergencyLeadAssignmentResponse:
    return EmergencyLeadAssignmentResponse(
        employee_id=value.employee_id,
        flight=flight_to_response(value.flight),
        reasons=value.reasons,
        message=value.message,
    )


def schedule_summary_to_response(value: ScheduleSummary) -> ScheduleSummaryResponse:
    return ScheduleSummaryResponse(
        total_flights=value.total_flights,
        minimum_staffed_flights=value.minimum_staffed_flights,
        below_minimum_flights=value.below_minimum_flights,
        preferred_staffed_flights=value.preferred_staffed_flights,
        qualification_required_flights=value.qualification_required_flights,
        qualification_compliant_flights=value.qualification_compliant_flights,
        missing_push_flights=value.missing_push_flights,
        missing_close_out_flights=value.missing_close_out_flights,
        participating_employee_count=value.participating_employee_count,
        employees_with_satisfied_break=value.employees_with_satisfied_break,
        employees_with_unsatisfied_break=value.employees_with_unsatisfied_break,
        employees_with_nonevaluable_break=value.employees_with_nonevaluable_break,
        total_assignments=value.total_assignments,
        emergency_lead_assignments=value.emergency_lead_assignments,
        critical_warning_count=value.critical_warning_count,
        warning_count=value.warning_count,
        all_objectives_proven_optimal=value.all_objectives_proven_optimal,
    )


def optimization_result_to_response(value: OptimizationResult) -> OptimizationResponse:
    """Map the complete public result without solver-specific implementation data."""

    return OptimizationResponse(
        status=value.status,
        operational_readiness=value.operational_readiness,
        emergency_staffing_status=value.emergency_staffing_status,
        emergency_pass_disposition=value.emergency_pass_disposition,
        emergency_leads_enabled=value.emergency_leads_enabled,
        emergency_lead_staffing_used=value.emergency_lead_staffing_used,
        solver_runtime_seconds=value.solver_runtime_seconds,
        schedule_summary=(
            schedule_summary_to_response(value.schedule_summary)
            if value.schedule_summary is not None
            else None
        ),
        flight_results=tuple(flight_result_to_response(item) for item in value.flight_results),
        employee_results=tuple(
            employee_result_to_response(item) for item in value.employee_results
        ),
        fairness_metrics=(
            fairness_to_response(value.fairness_metrics)
            if value.fairness_metrics is not None
            else None
        ),
        continuity_metrics=(
            continuity_to_response(value.continuity_metrics)
            if value.continuity_metrics is not None
            else None
        ),
        attempts=tuple(attempt_to_response(item) for item in value.attempts),
        objective_values=tuple(
            objective_to_response(item) for item in value.objective_values
        ),
        warnings=tuple(warning_to_response(item) for item in value.warnings),
        lead_assignments=tuple(
            lead_assignment_to_response(item) for item in value.lead_assignments
        ),
    )


def _resolve_flight_reference(
    reference: FlightReferenceRequest,
    flights: tuple[Flight, ...],
    fixed_index: int,
) -> tuple[Flight | None, ValidationIssue | None]:
    base_path = f"fixed_assignments[{fixed_index}].flight"
    try:
        identity = _reference_identity(
            reference.arrival_flight_number,
            reference.departure_flight_number,
        )
    except FlightNumberParseError as error:
        return None, ValidationIssue(
            "INVALID_FIXED_FLIGHT_REFERENCE",
            base_path,
            str(error),
        )
    if identity == (None, None):
        return None, ValidationIssue(
            "MISSING_FIXED_FLIGHT_REFERENCE",
            base_path,
            "must supply an arrival flight number, departure flight number, or both",
        )
    matches: list[Flight] = []
    for flight in flights:
        try:
            candidate_identity = _reference_identity(
                flight.arrival_flight_number,
                flight.departure_flight_number,
            )
        except FlightNumberParseError:
            continue
        if candidate_identity == identity:
            matches.append(flight)
    if not matches:
        return None, ValidationIssue(
            "UNRESOLVED_FIXED_FLIGHT_REFERENCE",
            base_path,
            "does not match a submitted flight by canonical directional identity",
        )
    if len(matches) > 1:
        return None, ValidationIssue(
            "AMBIGUOUS_FIXED_FLIGHT_REFERENCE",
            base_path,
            "matches more than one submitted flight by canonical directional identity",
        )
    return matches[0], None


def _reference_identity(
    arrival_flight_number: str | None,
    departure_flight_number: str | None,
) -> tuple[int | None, int | None]:
    return (
        parse_numeric_flight_number(arrival_flight_number)
        if arrival_flight_number is not None
        else None,
        parse_numeric_flight_number(departure_flight_number)
        if departure_flight_number is not None
        else None,
    )
