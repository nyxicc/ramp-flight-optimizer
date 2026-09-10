"""Explicit resource routes for complete, atomic input revisions."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from ramp_optimizer_api.input_schemas import DraftRequest, InputVersionResponse, RevisionRequest
from ramp_optimizer_api.input_services import InputService
from ramp_optimizer_api.schemas import ErrorResponse


def input_router(service: InputService) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        tags=["input versions"],
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )

    @router.post(
        "/operational-days/{day}/drafts", response_model=InputVersionResponse, status_code=201
    )
    def create(day: date, request: DraftRequest):
        return service.create(day, request)

    @router.get("/operational-days/{day}/versions", response_model=tuple[InputVersionResponse, ...])
    def listing(
        day: date,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return service.list(day, limit, offset)

    @router.get("/operational-day-versions/{version_id}", response_model=InputVersionResponse)
    def get(version_id: UUID):
        return service.get(str(version_id))

    @router.post(
        "/operational-day-versions/{version_id}/revisions",
        response_model=InputVersionResponse,
        status_code=201,
    )
    def revise(version_id: UUID, request: RevisionRequest):
        parent = service.get(str(version_id))
        return service.create(parent.operational_date, request, str(version_id))

    return router
