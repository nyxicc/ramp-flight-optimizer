"""Typed asynchronous optimization contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ramp_optimizer_api.schemas import (
    ApiModel,
    OptimizationResponse,
    OptimizerConfigRequest,
    StrictNumber,
)

JobStatus = Literal[
    "QUEUED", "RUNNING", "CANCELLING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"
]


class JobCreateRequest(ApiModel):
    operational_day_version_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    config: OptimizerConfigRequest | None = None
    timeout_seconds: StrictNumber = Field(default=180, gt=0, le=3600)


class JobProgress(ApiModel):
    phase: str
    pass_number: int = 0
    stage_number: int = 0
    stage_name: str | None = None
    solver_status: str | None = None


class JobResponse(ApiModel):
    id: UUID
    status: JobStatus
    operational_day_id: UUID
    operational_day_version_id: UUID | None
    input_hash: str
    config: OptimizerConfigRequest
    timeout_seconds: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: JobProgress
    error_code: str | None
    result_run_id: UUID | None
    has_partial_result: bool


class JobResultResponse(ApiModel):
    job_id: UUID
    status: JobStatus
    partial: bool
    result_run_id: UUID | None
    result: OptimizationResponse
