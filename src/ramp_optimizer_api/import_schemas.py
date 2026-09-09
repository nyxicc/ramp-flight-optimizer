"""Closed version 1 schemas for multipart metadata and explicit review edits."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ramp_optimizer.enums import IssueSeverity, OperationalRole, Qualification
from ramp_optimizer_api.schemas import ApiModel, EmployeeRequest, OptimizerConfigRequest
from ramp_optimizer_imports.enums import ImportStatus, ImportType


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

    @model_validator(mode='after')
    def validate_patch(self):
        if len(self.model_fields_set) < 2 or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError('Supply at least one non-null correction value.')
        if self.qualifications is not None and len(set(self.qualifications)) != len(self.qualifications):
            raise ValueError('Qualifications must be unique.')
        if self.vacancy is True and self.employee_id is not None:
            raise ValueError('A vacancy cannot also select an employee.')
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
    match_status: Literal['MATCHED', 'VACANCY', 'UNMATCHED_EMPLOYEE', 'AMBIGUOUS_EMPLOYEE']
    formula_fields: tuple[str, ...]
    required_fields_missing: tuple[str, ...]
    source_date: date | None
    imported_hours: float | None


class ImportPreviewResponse(ApiModel):
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
    discarded_sensitive_fields: tuple[str, ...] = ('notes', 'unmatched_employee_names')


class ImportResponse(ApiModel):
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
