"""Job admission and transactional persistence, without executing solvers in HTTP."""

import json
from hashlib import sha256
from importlib.metadata import version
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ramp_optimizer import validate_or_raise
from ramp_optimizer_api.errors import FixedAssignmentReferenceError
from ramp_optimizer_api.job_schemas import JobResponse, JobResultResponse
from ramp_optimizer_api.mapping import config_to_domain, map_optimization_request
from ramp_optimizer_api.schemas import OptimizationRequest, OptimizerConfigRequest
from ramp_optimizer_imports.models import ImportError
from ramp_optimizer_persistence.database import SessionFactory
from ramp_optimizer_persistence.errors import PersistenceConflictError
from ramp_optimizer_persistence.imports import SQLImportRepository, is_imported_snapshot
from ramp_optimizer_persistence.jobs import cancel_job, get_job, now, owned_job
from ramp_optimizer_persistence.models import InputVersionRow, JobRequestRow, OptimizationJobRow
from ramp_optimizer_persistence.repositories import (
    create_operational_day,
    create_optimization_run,
    get_operational_day,
)
from ramp_optimizer_persistence.serialization import canonical_input_hash, config_snapshot_json


class JobConflictError(PersistenceConflictError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JobService:
    def __init__(self, sessions: SessionFactory):
        self.sessions = sessions

    @staticmethod
    def response(row: OptimizationJobRow) -> JobResponse:
        return JobResponse.model_validate(
            dict(
                id=row.id,
                status=row.status,
                operational_day_id=row.operational_day_id,
                operational_day_version_id=row.source_version_id,
                input_hash=row.input_hash,
                config=json.loads(row.config_json),
                timeout_seconds=row.timeout_seconds,
                created_at=row.created_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                progress=json.loads(row.progress_json),
                error_code=row.error_code,
                result_run_id=row.result_run_id,
                has_partial_result=row.checkpoint_json is not None and row.status != "SUCCEEDED",
            )
        )

    def get(self, job_id: str) -> JobResponse:
        with self.sessions() as session:
            return self.response(get_job(session, job_id))

    def cancel(self, job_id: str) -> JobResponse:
        with self.sessions.begin() as session:
            cancel_job(session, job_id)
            return self.response(get_job(session, job_id))

    def result(self, job_id: str) -> JobResultResponse:
        with self.sessions() as session:
            row = get_job(session, job_id)
            if row.checkpoint_json is None:
                raise JobConflictError(
                    "JOB_RESULT_NOT_AVAILABLE", "No optimization result is available yet"
                )
            return JobResultResponse.model_validate(
                dict(
                    job_id=row.id,
                    status=row.status,
                    partial=row.status != "SUCCEEDED",
                    result_run_id=row.result_run_id,
                    result=json.loads(row.checkpoint_json),
                )
            )

    def submit(
        self,
        *,
        source_id: str | None = None,
        request: OptimizationRequest | None = None,
        key: str | None = None,
        config: OptimizerConfigRequest | None = None,
        timeout: float = 180,
        require_version: bool = False,
    ) -> JobResponse:
        for attempt in range(2):
            try:
                with self.sessions.begin() as session:
                    snapshot = get_operational_day(session, source_id) if source_id else None
                    source_version = session.get(InputVersionRow, source_id) if source_id else None
                    if require_version and source_version is None:
                        from ramp_optimizer_persistence.errors import ResourceNotFoundError

                        raise ResourceNotFoundError("Input version not found")
                    if snapshot:
                        if (
                            is_imported_snapshot(session, snapshot.id)
                            and not SQLImportRepository(session).readiness(snapshot.id)[
                                "optimization_eligible"
                            ]
                        ):
                            raise ImportError(
                                "FLIGHT_DATA_REQUIRED"
                                if not snapshot.day.flights
                                else "CONFIRMED_EMPLOYEE_SCHEDULE_REQUIRED",
                                409,
                            )
                        day = snapshot.day
                        active_config = config_to_domain(config) if config else snapshot.config
                    else:
                        assert request is not None
                        mapped = map_optimization_request(request)
                        if mapped.issues:
                            raise FixedAssignmentReferenceError(mapped.issues)
                        day, active_config = mapped.operational_day, mapped.config
                    validate_or_raise(day, active_config)
                    digest = canonical_input_hash(day, active_config)
                    fingerprint = sha256(
                        f"{source_id}:{digest}:{float(timeout)}".encode()
                    ).hexdigest()
                    request_key = key or f"legacy-{fingerprint}"
                    prior = session.get(JobRequestRow, request_key)
                    if prior:
                        if prior.request_hash != fingerprint:
                            raise JobConflictError(
                                "JOB_IDEMPOTENCY_KEY_REUSED",
                                "Idempotency key reused for different job inputs",
                            )
                        return self.response(get_job(session, prior.job_id))
                    row = session.scalar(
                        select(OptimizationJobRow).where(
                            OptimizationJobRow.active_key == fingerprint
                        )
                    )
                    if row is None:
                        pending = (
                            session.scalar(
                                select(func.count())
                                .select_from(OptimizationJobRow)
                                .where(
                                    OptimizationJobRow.status.in_(
                                        ("QUEUED", "RUNNING", "CANCELLING")
                                    )
                                )
                            )
                            or 0
                        )
                        if pending >= 1000:
                            raise JobConflictError(
                                "JOB_QUEUE_FULL", "Local optimization queue is full"
                            )
                        if snapshot is None or active_config != snapshot.config:
                            snapshot = create_operational_day(session, day, active_config)
                        row = OptimizationJobRow(
                            id=str(uuid4()),
                            operational_day_id=snapshot.id,
                            source_version_id=source_version.id if source_version else None,
                            input_hash=digest,
                            config_json=config_snapshot_json(active_config),
                            timeout_seconds=timeout,
                            active_key=fingerprint,
                            status="QUEUED",
                            created_at=now(),
                            progress_json=json.dumps({"phase": "QUEUED"}),
                        )
                        session.add(row)
                        session.flush()
                    session.add(
                        JobRequestRow(key=request_key, request_hash=fingerprint, job_id=row.id)
                    )
                    session.flush()
                    return self.response(row)
            except IntegrityError:
                if attempt:
                    raise JobConflictError(
                        "JOB_ADMISSION_CONFLICT", "Concurrent job admission; retry"
                    ) from None
        raise AssertionError("unreachable")

    def checkpoint(self, job_id: str, token: str, progress: dict, result: dict | None) -> None:
        from ramp_optimizer_api.job_schemas import JobProgress
        from ramp_optimizer_api.schemas import OptimizationResponse

        progress_json = JobProgress.model_validate(progress).model_dump_json()
        result_json = (
            OptimizationResponse.model_validate(result).model_dump_json() if result else None
        )
        with self.sessions.begin() as session:
            row = owned_job(session, job_id, token)
            if row:
                row.progress_json = progress_json
                if result_json:
                    row.checkpoint_json = result_json

    def finish(
        self,
        job_id: str,
        token: str,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        from ramp_optimizer_api.schemas import OptimizationResponse

        result_json = (
            OptimizationResponse.model_validate(result).model_dump_json() if result else None
        )
        with self.sessions.begin() as session:
            row = owned_job(session, job_id, token)
            if row is None:
                return
            if row.status == "CANCELLING":
                status = "CANCELLED"
            if result_json:
                row.checkpoint_json = result_json
            if row.checkpoint_json:
                snapshot = get_operational_day(session, row.operational_day_id)
                run = create_optimization_run(
                    session,
                    snapshot,
                    json.loads(row.checkpoint_json),
                    package_version=version("ramp-flight-optimizer"),
                    api_version="1",
                )
                row.result_run_id = run.id
            row.status, row.finished_at, row.active_key = status, now(), None
            row.error_code = error_code
            progress = json.loads(row.progress_json)
            progress["phase"] = status
            row.progress_json = json.dumps(progress)
