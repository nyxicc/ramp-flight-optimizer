"""Versioned reviewed-import routes and bounded multipart request handling."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Path, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ramp_optimizer.timing import FlightDerivationError, derive_flight_operational_facts
from ramp_optimizer_api.import_schemas import (
    CombineImportsRequest,
    ConfirmImportRequest,
    CorrectionsRequest,
    FlightCorrectionsRequest,
    FlightImportMetadataRequest,
    ImportMetadataRequest,
    ImportReadinessResponse,
    ImportResponse,
)
from ramp_optimizer_api.mapping import config_to_domain, employee_to_domain
from ramp_optimizer_imports.daily_flight_log import CORRECTABLE
from ramp_optimizer_imports.enums import ImportType
from ramp_optimizer_imports.flight_models import FlightCorrection, FlightTimePolicy
from ramp_optimizer_imports.models import ImportError, RowCorrection
from ramp_optimizer_imports.safety import read_upload, validate_filename
from ramp_optimizer_imports.serialization import json_value, preview_counts


def import_response(record) -> ImportResponse:
    value = json_value(record)
    preview = record.preview
    rows = preview.rows
    flight_rows = []
    for row in preview.flight_rows:
        value_row = json_value(row)
        value_row.update(
            confirmation_eligible=not any(
                i.blocks_confirmation and i.source_row in {None, row.source_row}
                for i in preview.issues
            ),
            will_be_excluded=row.excluded or row.status == "CANCELLED",
            correctable_fields=sorted(CORRECTABLE),
        )
        try:
            facts = derive_flight_operational_facts(row.flight, preview.config)
            value_row.update(
                movement_type=facts.flight_type.value,
                express=facts.express,
                work_start=facts.work_start,
                work_end=facts.work_end,
            )
        except FlightDerivationError:
            pass
        flight_rows.append(value_row)
    value.update(preview_counts(preview))
    value["accepted_flight_count"] = sum(
        r["confirmation_eligible"] and not r["will_be_excluded"] for r in flight_rows
    )
    value["preview"] = {
        "flight_rows": flight_rows,
        "flight_policy": json_value(preview.flight_policy),
        "flight_corrections": json_value(preview.flight_corrections),
        "operational_date_correction": preview.operational_date_correction,
        "revision": preview.revision,
        "operational_date": preview.operational_date,
        "detected_operational_dates": sorted(
            {r.values.source_date for r in rows if r.values.source_date}
        ),
        "roster": json_value(preview.roster),
        "employees": json_value(preview.employees),
        "rows": [{"row_id": r.row_id, **json_value(r.values)} for r in rows],
        "matched_employee_ids": sorted(
            {r.values.employee_id for r in rows if r.values.employee_id and not r.values.excluded}
        ),
        "unmatched_row_ids": [
            r.row_id
            for r in rows
            if r.values.match_status == "UNMATCHED_EMPLOYEE" and not r.values.excluded
        ],
        "ambiguous_row_ids": [
            r.row_id
            for r in rows
            if r.values.match_status == "AMBIGUOUS_EMPLOYEE" and not r.values.excluded
        ],
        "vacancy_row_ids": [r.row_id for r in rows if r.values.vacancy and not r.values.excluded],
        "issues": json_value(preview.issues),
        "source_issues": json_value(preview.source_issues),
        "confirmation_eligible": preview.confirmation_eligible,
        "confirmation_blockers": json_value(
            tuple(i for i in preview.issues if i.blocks_confirmation)
        ),
        "optimization_blockers": [
            {
                "code": "FLIGHT_DATA_REQUIRED",
                "severity": "ERROR",
                "message": "A later operational-day snapshot requires flight data before optimization.",
                "field": "flights",
                "blocks_confirmation": False,
            }
        ],
        "config": json_value(preview.config),
        "role_mappings": preview.import_config.position_role_mappings,
        "discarded_sensitive_fields": ("notes",)
        if any(r.values.employee_name for r in rows)
        else ("notes", "unmatched_employee_names"),
    }
    if record.import_type == ImportType.DAILY_FLIGHT_LOG:
        value["preview"]["optimization_blockers"] = [
            {
                "code": "CONFIRMED_EMPLOYEE_SCHEDULE_REQUIRED",
                "severity": "ERROR",
                "message": "Combine the confirmed flight log with a confirmed employee schedule.",
                "field": "employee_shifts",
                "blocks_confirmation": False,
            }
        ]
    return ImportResponse.model_validate(value)


def import_router(service):
    router = APIRouter(prefix="/api/v1/imports", tags=["reviewed imports"])

    @router.post("/combine", response_model=ImportReadinessResponse)
    def combine_imports(request: CombineImportsRequest):
        return service.combine(str(request.employee_import_id), str(request.flight_import_id))

    @router.get("/operational-days/{day_id}/readiness", response_model=ImportReadinessResponse)
    def readiness(day_id: UUID):
        return service.readiness(str(day_id))

    @router.post("/daily-flight-log", response_model=ImportResponse, status_code=201)
    async def upload_flights(
        workbook: Annotated[UploadFile, File()], metadata: Annotated[str, Form()]
    ):
        try:
            filename, media = validate_filename(workbook.filename, workbook.content_type)
            if len(metadata.encode("utf-8")) > 256 * 1024:
                raise ImportError("IMPORT_METADATA_TOO_LARGE", 413)
            try:
                request = FlightImportMetadataRequest.model_validate_json(metadata)
            except ValidationError:
                raise ImportError("REQUEST_VALIDATION_ERROR") from None
            content, digest = await read_upload(workbook, service.limits)
            record = await run_in_threadpool(
                service.upload_flights,
                content,
                filename,
                media,
                request.operational_date,
                FlightTimePolicy(**request.time_policy.model_dump()),
                config_to_domain(request.config),
                digest=digest,
            )
            return import_response(record)
        finally:
            await workbook.close()

    @router.post("/teamwork-employee-schedule", response_model=ImportResponse, status_code=201)
    async def upload_schedule(
        workbook: Annotated[UploadFile, File(description="TeamWork .xlsx workbook")],
        metadata: Annotated[
            str,
            Form(
                description="JSON ImportMetadataRequest: operational_date, authoritative roster, optional config"
            ),
        ],
    ):
        try:
            filename, media = validate_filename(workbook.filename, workbook.content_type)
            if len(metadata.encode("utf-8")) > 256 * 1024:
                raise ImportError("IMPORT_METADATA_TOO_LARGE", 413)
            try:
                request = ImportMetadataRequest.model_validate_json(metadata)
            except ValidationError:
                raise ImportError("REQUEST_VALIDATION_ERROR") from None
            content, digest = await read_upload(workbook, service.limits)
            record = await run_in_threadpool(
                service.upload,
                content,
                filename,
                media,
                request.operational_date,
                tuple(employee_to_domain(e) for e in request.roster),
                config_to_domain(request.config),
                digest=digest,
                ramp_agents_only=request.ramp_agents_only,
            )
            return import_response(record)
        finally:
            await workbook.close()

    @router.get("/{import_id}", response_model=ImportResponse)
    @router.get("/{import_id}/preview", response_model=ImportResponse)
    def get_import(import_id: UUID):
        return import_response(service.get(str(import_id)))

    @router.get("/{import_id}/revisions/{revision}", response_model=ImportResponse)
    def get_revision(import_id: UUID, revision: Annotated[int, Path(ge=1)]):
        return import_response(service.get_revision(str(import_id), revision))

    @router.post("/{import_id}/corrections", response_model=ImportResponse)
    def correct_import(import_id: UUID, request: CorrectionsRequest | FlightCorrectionsRequest):
        record = service.get(str(import_id))
        if isinstance(request, FlightCorrectionsRequest):
            if record.import_type != ImportType.DAILY_FLIGHT_LOG:
                raise ImportError("INVALID_IMPORT_CORRECTIONS")
            changes = tuple(
                FlightCorrection(
                    str(c.row_id),
                    tuple(
                        (key, value)
                        for key, value in c.model_dump(mode="json", exclude_unset=True).items()
                        if key != "row_id"
                    ),
                )
                for c in request.flight_corrections
            )
            return import_response(
                service.correct(
                    str(import_id),
                    request.revision,
                    changes,
                    operational_date=request.operational_date,
                )
            )
        if record.import_type != ImportType.TEAMWORK_EMPLOYEE_SCHEDULE:
            raise ImportError("INVALID_IMPORT_CORRECTIONS")
        corrections = tuple(
            RowCorrection(
                **{
                    **c.model_dump(),
                    "row_id": str(c.row_id),
                    "qualifications": frozenset(c.qualifications)
                    if c.qualifications is not None
                    else None,
                }
            )
            for c in request.corrections
        )
        return import_response(service.correct(str(import_id), request.revision, corrections))

    @router.post("/{import_id}/confirm", response_model=ImportResponse)
    def confirm_import(import_id: UUID, request: ConfirmImportRequest):
        return import_response(service.confirm(str(import_id), request.revision))

    return router


class BoundedImportBody:
    """Cap multipart body before Starlette can spool an unbounded upload to disk.

    Buffering is bounded to workbook limit + metadata/encoding allowance. Normal
    UploadFile spools are owned and removed by Starlette's request form context.
    """

    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes + 256 * 1024

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or (
                scope.get("path")
                not in {
                    "/api/v1/imports/teamwork-employee-schedule",
                    "/api/v1/imports/daily-flight-log",
                    "/api/v1/optimization-jobs",
                    "/api/v1/optimizations",
                }
                and not (
                    scope.get("path", "").startswith("/api/v1/operational-")
                    and scope.get("path", "").endswith(("/drafts", "/revisions"))
                )
            )
        ):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "UPLOAD_TOO_LARGE",
                            "message": "Import upload exceeds the configured limit.",
                            "details": [],
                        }
                    },
                )
                return await response(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
