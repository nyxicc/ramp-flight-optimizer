"""Explicit Pydantic models for the version 1 HTTP contract."""

from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from ramp_optimizer import (
    BreakStatus,
    EmergencyLeadReason,
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    FlightType,
    OperationalReadinessStatus,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
)


class ApiModel(BaseModel):
    """Shared closed and immutable API model behavior."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class EmployeeRequest(ApiModel):
    employee_id: StrictStr
    name: StrictStr
    qualifications: tuple[Qualification, ...] = ()
    enabled: StrictBool = True

    @model_validator(mode="after")
    def qualifications_must_be_unique(self) -> "EmployeeRequest":
        if len(set(self.qualifications)) != len(self.qualifications):
            raise ValueError("qualifications must not contain duplicates")
        return self


class EmployeeShiftRequest(ApiModel):
    employee_id: StrictStr
    start: datetime
    end: datetime
    normalized_role: OperationalRole


class FlightRequest(ApiModel):
    arrival_flight_number: StrictStr | None = None
    arrival_time: datetime | None = None
    departure_flight_number: StrictStr | None = None
    departure_time: datetime | None = None
    gate: StrictStr | None = None
    heavy: StrictBool = False


class FlightReferenceRequest(ApiModel):
    arrival_flight_number: StrictStr | None = None
    departure_flight_number: StrictStr | None = None


class FixedAssignmentRequest(ApiModel):
    employee_id: StrictStr
    flight: FlightReferenceRequest


class OperationalDayRequest(ApiModel):
    operational_date: date
    employees: tuple[EmployeeRequest, ...] = ()
    employee_shifts: tuple[EmployeeShiftRequest, ...] = ()
    flights: tuple[FlightRequest, ...] = ()
    fixed_assignments: tuple[FixedAssignmentRequest, ...] = ()


_CONFIG_DEFAULTS = OptimizerConfig()


def _require_json_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a JSON number")
    return value


StrictNumber = Annotated[float, BeforeValidator(_require_json_number)]


class OptimizerConfigRequest(ApiModel):
    """Every public optimizer setting, defaulted from the domain configuration."""

    arrival_preparation_minutes: StrictInt = _CONFIG_DEFAULTS.arrival_preparation_minutes
    arrival_offload_minutes: StrictInt = _CONFIG_DEFAULTS.arrival_offload_minutes
    departure_work_minutes: StrictInt = _CONFIG_DEFAULTS.departure_work_minutes
    minimum_staff: StrictInt = _CONFIG_DEFAULTS.minimum_staff
    normal_preferred_staff: StrictInt = _CONFIG_DEFAULTS.normal_preferred_staff
    heavy_preferred_staff: StrictInt = _CONFIG_DEFAULTS.heavy_preferred_staff
    required_break_minutes: StrictInt = _CONFIG_DEFAULTS.required_break_minutes
    consecutive_reset_minutes: StrictInt = _CONFIG_DEFAULTS.consecutive_reset_minutes
    continuity_horizon_minutes: StrictInt = _CONFIG_DEFAULTS.continuity_horizon_minutes
    express_threshold: StrictInt = _CONFIG_DEFAULTS.express_threshold
    express_workload_factor: StrictNumber = _CONFIG_DEFAULTS.express_workload_factor
    three_person_workload_multiplier: StrictNumber = (
        _CONFIG_DEFAULTS.three_person_workload_multiplier
    )
    workload_scale: StrictInt = _CONFIG_DEFAULTS.workload_scale
    allow_leads_for_minimum_staffing: StrictBool = (
        _CONFIG_DEFAULTS.allow_leads_for_minimum_staffing
    )
    allow_trainees_for_assignments: StrictBool = (
        _CONFIG_DEFAULTS.allow_trainees_for_assignments
    )
    allow_possible_ramp_support_for_assignments: StrictBool = (
        _CONFIG_DEFAULTS.allow_possible_ramp_support_for_assignments
    )
    solver_time_limit_seconds: StrictNumber = _CONFIG_DEFAULTS.solver_time_limit_seconds
    solver_random_seed: StrictInt = _CONFIG_DEFAULTS.solver_random_seed
    solver_num_search_workers: StrictInt = _CONFIG_DEFAULTS.solver_num_search_workers


class OptimizationRequest(ApiModel):
    operational_day: OperationalDayRequest
    config: OptimizerConfigRequest = Field(default_factory=OptimizerConfigRequest)


class ValidationIssueResponse(ApiModel):
    code: str
    path: str
    message: str


class ValidationResponse(ApiModel):
    valid: bool
    issues: tuple[ValidationIssueResponse, ...]


class HealthResponse(ApiModel):
    status: str


class VersionResponse(ApiModel):
    api_version: str
    package_version: str


class FlightResponse(ApiModel):
    arrival_flight_number: str | None
    arrival_time: datetime | None
    departure_flight_number: str | None
    departure_time: datetime | None
    gate: str | None
    heavy: bool


class ScheduleWarningResponse(ApiModel):
    code: WarningCode
    severity: WarningSeverity
    message: str
    arrival_flight_number: str | None
    departure_flight_number: str | None
    employee_id: str | None


class FlightAssignmentResponse(ApiModel):
    flight: FlightResponse
    flight_type: FlightType
    work_start: datetime
    work_end: datetime
    assigned_employee_ids: tuple[str, ...]
    fixed_employee_ids: tuple[str, ...]
    staffing_count: int
    minimum_staff: int
    preferred_staff: int
    maximum_staff: int
    staffing_status: StaffingStatus
    minimum_met: bool
    minimum_shortfall: int
    preferred_met: bool
    preferred_shortfall: int
    express: bool
    heavy: bool
    push_covered: bool | None
    close_covered: bool | None
    warnings: tuple[ScheduleWarningResponse, ...]


class EmployeeScheduleResponse(ApiModel):
    employee_id: str
    assigned_flights: tuple[FlightResponse, ...]
    flight_count: int
    mainline_flight_count: int
    express_flight_count: int
    three_person_flight_count: int
    longest_consecutive_streak: int
    break_status: BreakStatus
    adjusted_workload: float | None
    scheduled_shift_minutes: int | None
    proportional_target_flight_count: float | None
    shift_adjusted_deviation: float | None


class FairnessMetricsResponse(ApiModel):
    participating_employee_count: int
    total_assignments: int
    average_flights: float
    highest_flight_count: int
    lowest_flight_count: int
    flight_count_spread: int
    maximum_consecutive_streak: int
    adjusted_workload_spread: float | None
    total_participating_shift_minutes: int
    total_shift_adjusted_deviation: float


class ContinuityTransitionResponse(ApiModel):
    previous_flight: FlightResponse
    next_flight: FlightResponse
    retained_employee_ids: tuple[str, ...]
    retention_count: int


class ContinuityMetricsResponse(ApiModel):
    eligible_transition_count: int
    total_retained_employee_transitions: int
    average_retained_employees_per_transition: float
    strongest_retention_count: int
    strongest_transition: ContinuityTransitionResponse | None
    transitions: tuple[ContinuityTransitionResponse, ...]


class ObjectiveValueResponse(ApiModel):
    stage: int
    name: str
    value: int
    proven_optimal: bool


class OptimizationAttemptResponse(ApiModel):
    included_leads: bool
    status: OptimizationStatus
    minimum_staffed_flights: int
    qualification_compliant_flights: int
    lead_assignments: int
    critical_shortage_count: int
    lead_candidate_count: int
    solver_runtime_seconds: float
    pass_number: int
    attempt_label: str
    usable_schedule: bool
    selected_as_final: bool
    known_unsatisfied_required_break_count: int
    objective_stages_completed: int
    all_objectives_proven_optimal: bool


class EmergencyLeadAssignmentResponse(ApiModel):
    employee_id: str
    flight: FlightResponse
    reasons: tuple[EmergencyLeadReason, ...]
    message: str


class ScheduleSummaryResponse(ApiModel):
    total_flights: int
    minimum_staffed_flights: int
    below_minimum_flights: int
    preferred_staffed_flights: int
    qualification_required_flights: int
    qualification_compliant_flights: int
    missing_push_flights: int
    missing_close_out_flights: int
    participating_employee_count: int
    employees_with_satisfied_break: int
    employees_with_unsatisfied_break: int
    employees_with_nonevaluable_break: int
    total_assignments: int
    emergency_lead_assignments: int
    critical_warning_count: int
    warning_count: int
    all_objectives_proven_optimal: bool


class OptimizationResponse(ApiModel):
    status: OptimizationStatus
    operational_readiness: OperationalReadinessStatus
    emergency_staffing_status: EmergencyStaffingStatus
    emergency_pass_disposition: EmergencyPassDisposition
    emergency_leads_enabled: bool
    emergency_lead_staffing_used: bool | None
    solver_runtime_seconds: float
    schedule_summary: ScheduleSummaryResponse | None
    flight_results: tuple[FlightAssignmentResponse, ...]
    employee_results: tuple[EmployeeScheduleResponse, ...]
    fairness_metrics: FairnessMetricsResponse | None
    continuity_metrics: ContinuityMetricsResponse | None
    attempts: tuple[OptimizationAttemptResponse, ...]
    objective_values: tuple[ObjectiveValueResponse, ...]
    warnings: tuple[ScheduleWarningResponse, ...]
    lead_assignments: tuple[EmergencyLeadAssignmentResponse, ...]


class ErrorDetail(ApiModel):
    code: str
    path: str
    message: str


class ErrorBody(ApiModel):
    code: str
    message: str
    details: tuple[ErrorDetail, ...] = ()


class ErrorResponse(ApiModel):
    error: ErrorBody
