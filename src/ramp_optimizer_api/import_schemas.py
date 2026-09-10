"""Closed version 1 schemas for multipart metadata and explicit review edits."""

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ramp_optimizer.enums import IssueSeverity, OperationalRole, Qualification
from ramp_optimizer_api.schemas import ApiModel, EmployeeRequest, OptimizerConfigRequest
from ramp_optimizer_imports.enums import ImportStatus, ImportType
from ramp_optimizer_imports.flight_models import FlightStatus


class FlightTimePolicyRequest(ApiModel):
    airport_timezone: str = Field(min_length=1, max_length=100)
    operational_day_start: time
    planning_basis: Literal["SCHEDULED", "ESTIMATED"]


class CombineImportsRequest(ApiModel):
    employee_import_id: UUID
    flight_import_id: UUID


class ImportReadinessResponse(ApiModel):
    operational_day_id: UUID
    confirmed_employee_schedule: bool
    confirmed_flight_log: bool
    both_inputs_confirmed: bool
    unresolved_blocking_issues: bool
    optimization_eligible: bool


class FlightImportMetadataRequest(ApiModel):
    operational_date: date
    time_policy: FlightTimePolicyRequest
    config: OptimizerConfigRequest = Field(default_factory=OptimizerConfigRequest)


class FlightCorrectionRequest(ApiModel):
    row_id: UUID
    arrival_flight_number: StrictStr | None = Field(default=None, max_length=40)
    departure_flight_number: StrictStr | None = Field(default=None, max_length=40)
    arrival_time: datetime | None = None
    departure_time: datetime | None = None
    origin: StrictStr | None = Field(default=None, max_length=3)
    destination: StrictStr | None = Field(default=None, max_length=3)
    gate: StrictStr | None = Field(default=None, max_length=16)
    status: FlightStatus | None = None
    heavy: StrictBool | None = None
    excluded: StrictBool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if len(self.model_fields_set) < 2 or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Supply at least one non-null correction value.")
        return self


class FlightCorrectionsRequest(ApiModel):
    revision: StrictInt = Field(ge=1)
    operational_date: date | None = None
    flight_corrections: tuple[FlightCorrectionRequest, ...] = Field(default=(), max_length=5000)

    @model_validator(mode="after")
    def require_change(self):
        if not self.flight_corrections and self.operational_date is None:
            raise ValueError("Supply flight corrections or an operational date.")
        return self


class FlightValuesResponse(ApiModel):
    arrival_flight_number: str | None
    departure_flight_number: str | None
    arrival_time: datetime | None
    departure_time: datetime | None
    gate: str | None
    heavy: bool


class FlightReviewRowResponse(ApiModel):
    movement_type: str | None = None
    express: bool | None = None
    work_start: datetime | None = None
    work_end: datetime | None = None
    confirmation_eligible: bool
    will_be_excluded: bool
    correctable_fields: tuple[str, ...]
    row_id: UUID
    source_row: int
    flight: FlightValuesResponse
    origin: str | None
    destination: str | None
    status: FlightStatus
    excluded: bool
    heavy_reviewed: bool
    notes_present: bool
    onward_time_present: bool
    late_arrival_section: bool
    arrival_expected: bool
    departure_expected: bool
    unresolved_fields: tuple[str, ...]
    formula_fields: tuple[str, ...]
    normalized_fields: tuple[str, ...]


class FlightCorrectionAuditResponse(ApiModel):
    row_id: UUID
    changes: tuple[tuple[str, str | bool | None], ...]
    original_values: tuple[tuple[str, str | bool | None], ...]


class ImportMetadataRequest(ApiModel):
    operational_date: date
    roster: tuple[EmployeeRequest, ...] = Field(max_length=5000)
    config: OptimizerConfigRequest = Field(default_factory=OptimizerConfigRequest)


class RowCorrectionRequest(ApiModel):
    row_id: UUID
    employee_id: StrictStr | None = None
    start: datetime | None = None
    end: datetime | None = None
    normalized_role: OperationalRole | None = None
    excluded: StrictBool | None = None
    vacancy: StrictBool | None = None
    enabled: StrictBool | None = None
    qualifications: tuple[Qualification, ...] | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if len(self.model_fields_set) < 2 or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Supply at least one non-null correction value.")
        if self.qualifications is not None and len(set(self.qualifications)) != len(
            self.qualifications
        ):
            raise ValueError("Qualifications must be unique.")
        if self.vacancy is True and self.employee_id is not None:
            raise ValueError("A vacancy cannot also select an employee.")
        return self


class CorrectionsRequest(ApiModel):
    revision: StrictInt = Field(ge=1)
    corrections: tuple[RowCorrectionRequest, ...] = Field(min_length=1, max_length=5000)


class ConfirmImportRequest(ApiModel):
    revision: StrictInt = Field(ge=1)


class ImportIssueResponse(ApiModel):
    code: str
    severity: IssueSeverity
    message: str
    source_row: int | None = None
    field: str | None = None
    blocks_confirmation: bool
    remediation: str | None = None


class ReviewRowResponse(ApiModel):
    row_id: UUID
    source_row: int
    employee_id: str | None
    start: datetime | None
    end: datetime | None
    normalized_role: OperationalRole
    vacancy: bool
    excluded: bool
    notes_present: bool
    swapboard: bool | None
    match_status: Literal["MATCHED", "VACANCY", "UNMATCHED_EMPLOYEE", "AMBIGUOUS_EMPLOYEE"]
    formula_fields: tuple[str, ...]
    required_fields_missing: tuple[str, ...]
    source_date: date | None
    imported_hours: float | None


class ImportPreviewResponse(ApiModel):
    flight_rows: tuple[FlightReviewRowResponse, ...] = ()
    flight_policy: FlightTimePolicyRequest | None = None
    flight_corrections: tuple[FlightCorrectionAuditResponse, ...] = ()
    operational_date_correction: tuple[date, date] | None = None
    revision: int
    operational_date: date
    detected_operational_dates: tuple[date, ...]
    roster: tuple[EmployeeRequest, ...]
    employees: tuple[EmployeeRequest, ...]
    rows: tuple[ReviewRowResponse, ...]
    matched_employee_ids: tuple[str, ...]
    unmatched_row_ids: tuple[UUID, ...]
    ambiguous_row_ids: tuple[UUID, ...]
    vacancy_row_ids: tuple[UUID, ...]
    issues: tuple[ImportIssueResponse, ...]
    source_issues: tuple[ImportIssueResponse, ...]
    confirmation_eligible: bool
    confirmation_blockers: tuple[ImportIssueResponse, ...]
    optimization_eligible: Literal[False] = False
    optimization_blockers: tuple[ImportIssueResponse, ...]
    config: OptimizerConfigRequest
    role_mappings: tuple[tuple[str, OperationalRole], ...]
    discarded_sensitive_fields: tuple[str, ...] = ("notes", "unmatched_employee_names")


class ImportResponse(ApiModel):
    accepted_flight_count: int = 0
    import_id: UUID
    import_type: ImportType
    status: ImportStatus
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    schema_version: int
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    confirmed_operational_day_id: UUID | None
    fatal_count: int
    error_count: int
    warning_count: int
    accepted_shift_count: int
    vacancy_count: int
    unresolved_row_count: int
    preview: ImportPreviewResponse
