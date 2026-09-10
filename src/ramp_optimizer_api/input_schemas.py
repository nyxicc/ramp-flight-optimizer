"""Atomic complete-snapshot edits for supervisor input management."""

from datetime import date, datetime
from uuid import UUID

from pydantic import Field, model_validator

from ramp_optimizer_api.schemas import ApiModel, OptimizationRequest, ValidationIssueResponse


class DraftRequest(ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_snapshot_id: UUID | None = None
    source_version_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def one_source(self) -> "DraftRequest":
        if self.source_snapshot_id and self.source_version_id:
            raise ValueError("Specify only one source")
        return self


class RevisionRequest(ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_parent_hash: str = Field(pattern="^[0-9a-f]{64}$")
    input: OptimizationRequest
    reason: str | None = Field(default=None, max_length=1000)


class InputVersionResponse(ApiModel):
    id: UUID
    operational_date: date
    version_number: int
    parent_version_id: UUID | None
    source_snapshot_id: UUID | None
    created_at: datetime
    content_hash: str
    input: OptimizationRequest
    reason: str | None
    warnings: tuple[ValidationIssueResponse, ...]
    valid: bool = True
