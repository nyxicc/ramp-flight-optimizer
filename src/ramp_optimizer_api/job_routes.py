"""Asynchronous optimization resources."""

from uuid import UUID

from fastapi import APIRouter

from ramp_optimizer_api.job_schemas import (
    JobCreateRequest,
    JobProgress,
    JobResponse,
    JobResultResponse,
)
from ramp_optimizer_api.job_services import JobService
from ramp_optimizer_api.schemas import ErrorResponse


def job_router(service: JobService) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/optimization-jobs",
        tags=["optimization jobs"],
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )

    @router.post("", response_model=JobResponse, status_code=202)
    def create(request: JobCreateRequest):
        return service.submit(
            source_id=str(request.operational_day_version_id),
            key=request.idempotency_key,
            config=request.config,
            timeout=request.timeout_seconds,
            require_version=True,
        )

    @router.get("/{job_id}", response_model=JobResponse)
    def status(job_id: UUID):
        return service.get(str(job_id))

    @router.get("/{job_id}/progress", response_model=JobProgress)
    def progress(job_id: UUID):
        return service.get(str(job_id)).progress

    @router.post("/{job_id}/cancel", response_model=JobResponse)
    def cancel(job_id: UUID):
        return service.cancel(str(job_id))

    @router.get("/{job_id}/result", response_model=JobResultResponse)
    def result(job_id: UUID):
        return service.result(str(job_id))

    return router
