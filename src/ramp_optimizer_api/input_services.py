"""Validate candidate snapshots before atomically appending immutable versions."""

import json
from datetime import date
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from ramp_optimizer import InputValidationError, ValidationIssue, validate_or_raise
from ramp_optimizer.eligibility import assess_employee_flight_eligibility
from ramp_optimizer.staffing import staffing_requirements_for
from ramp_optimizer_api.input_schemas import DraftRequest, InputVersionResponse, RevisionRequest
from ramp_optimizer_api.mapping import map_optimization_request, operational_day_to_request
from ramp_optimizer_api.schemas import OperationalDayRequest, OptimizationRequest
from ramp_optimizer_persistence.database import SessionFactory
from ramp_optimizer_persistence.errors import PersistenceConflictError
from ramp_optimizer_persistence.imports import is_imported_snapshot
from ramp_optimizer_persistence.models import InputVersionRow
from ramp_optimizer_persistence.repositories import create_operational_day, get_operational_day
from ramp_optimizer_persistence.versions import append_version, get_version, versions_for_date


class InputConflictError(PersistenceConflictError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class InputService:
    def __init__(self, sessions: SessionFactory):
        self.sessions = sessions

    def response(self, session, row: InputVersionRow) -> InputVersionResponse:
        snapshot = get_operational_day(session, row.id)
        return InputVersionResponse(
            id=UUID(row.id),
            operational_date=row.operational_date,
            version_number=row.version_number,
            parent_version_id=UUID(row.parent_version_id) if row.parent_version_id else None,
            source_snapshot_id=UUID(row.source_snapshot_id) if row.source_snapshot_id else None,
            created_at=snapshot.created_at_utc,
            content_hash=snapshot.input_hash,
            input=operational_day_to_request(snapshot.day, snapshot.config),
            reason=row.reason,
            warnings=json.loads(row.validation_json),
        )

    def get(self, version_id: str) -> InputVersionResponse:
        with self.sessions() as session:
            return self.response(session, get_version(session, version_id))

    def list(self, day: date, limit: int, offset: int) -> tuple[InputVersionResponse, ...]:
        with self.sessions() as session:
            return tuple(
                self.response(session, row)
                for row in session.scalars(
                    versions_for_date(session, day).limit(limit).offset(offset)
                )
            )

    def create(
        self, day: date, request: DraftRequest | RevisionRequest, parent_id: str | None = None
    ) -> InputVersionResponse:
        fingerprint = sha256(
            json.dumps(
                {"parent": parent_id, "request": request.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        # A uniqueness race rolls back the entire snapshot and retries the idempotency lookup.
        for attempt in range(2):
            try:
                with self.sessions.begin() as session:
                    existing = session.scalar(
                        select(InputVersionRow).where(
                            InputVersionRow.operational_date == day,
                            InputVersionRow.idempotency_key == request.idempotency_key,
                        )
                    )
                    if existing:
                        if existing.request_hash != fingerprint:
                            raise InputConflictError(
                                "IDEMPOTENCY_KEY_REUSED",
                                "Idempotency key reused with different content",
                            )
                        return self.response(session, existing)
                    rows = list(session.scalars(versions_for_date(session, day)))
                    source = None
                    if isinstance(request, RevisionRequest):
                        parent = get_version(session, parent_id or "")
                        previous = get_operational_day(session, parent.id)
                        if (
                            not rows
                            or rows[-1].id != parent.id
                            or previous.input_hash != request.expected_parent_hash
                        ):
                            raise InputConflictError(
                                "STALE_INPUT_VERSION",
                                "Stale parent version; retrieve the latest version",
                            )
                        candidate = request.input
                        removed = {e.employee_id for e in previous.day.employees} - {
                            e.employee_id for e in candidate.operational_day.employees
                        }
                        if removed:
                            self.invalid(
                                "EMPLOYEE_REMOVAL_FORBIDDEN",
                                "employees",
                                "Disable employees instead of removing them",
                            )
                    else:
                        parent_id = (
                            str(request.source_version_id) if request.source_version_id else None
                        )
                        source = (
                            str(request.source_snapshot_id) if request.source_snapshot_id else None
                        )
                        if parent_id:
                            get_version(session, parent_id)
                        if source and not is_imported_snapshot(session, source):
                            get_operational_day(session, source)
                            self.invalid(
                                "CONFIRMED_IMPORT_REQUIRED",
                                "source_snapshot_id",
                                "Source must be a confirmed import snapshot",
                            )
                        if parent_id or source:
                            snapshot = get_operational_day(session, parent_id or source or "")
                            candidate = operational_day_to_request(snapshot.day, snapshot.config)
                        else:
                            candidate = OptimizationRequest(
                                operational_day=OperationalDayRequest(operational_date=day)
                            )
                    if candidate.operational_day.operational_date != day:
                        self.invalid(
                            "OPERATIONAL_DATE_MISMATCH",
                            "operational_date",
                            "Source and candidate must match the route date",
                        )
                    data = candidate.operational_day
                    for field in ("employees", "employee_shifts", "flights", "fixed_assignments"):
                        if len(getattr(data, field)) > 1000:
                            self.invalid(
                                "INPUT_LIMIT_EXCEEDED", field, "At most 1000 records per collection"
                            )
                    for index, shift in enumerate(data.employee_shifts):
                        if shift.start.date() != day or (shift.end.date() - day).days not in (0, 1):
                            self.invalid(
                                "SHIFT_DATE_BOUNDARY",
                                f"employee_shifts[{index}]",
                                "Shift must start on the operational date and end that day or the next",
                            )
                    for index, flight in enumerate(data.flights):
                        for field in ("arrival_time", "departure_time"):
                            instant = getattr(flight, field)
                            if instant and (instant.date() - day).days not in (0, 1):
                                self.invalid(
                                    "FLIGHT_DATE_BOUNDARY",
                                    f"flights[{index}].{field}",
                                    "Flight time must be on the operational date or the next day",
                                )
                    mapped = map_optimization_request(candidate)
                    if mapped.issues:
                        raise InputValidationError(mapped.issues)
                    validate_or_raise(mapped.operational_day, mapped.config)
                    warnings = []
                    if not mapped.operational_day.flights:
                        warnings.append(
                            dict(
                                code="FLIGHT_DATA_REQUIRED",
                                path="flights",
                                message="Draft has no flights",
                            )
                        )
                    if not any(e.enabled for e in mapped.operational_day.employees):
                        warnings.append(
                            dict(
                                code="EMPLOYEE_DATA_REQUIRED",
                                path="employees",
                                message="Draft has no enabled employees",
                            )
                        )
                    for index, domain_flight in enumerate(mapped.operational_day.flights):
                        eligible = [
                            e
                            for e in mapped.operational_day.employees
                            if assess_employee_flight_eligibility(
                                e,
                                mapped.operational_day.employee_shifts,
                                domain_flight,
                                mapped.config,
                                mapped.operational_day.fixed_assignments,
                            ).eligible
                        ]
                        if (
                            len(eligible)
                            < staffing_requirements_for(domain_flight, mapped.config).minimum
                        ):
                            warnings.append(
                                dict(
                                    code="INSUFFICIENT_ELIGIBLE_STAFF",
                                    path=f"flights[{index}]",
                                    message="Eligible employee pool is below minimum staffing; optimization may be incomplete",
                                )
                            )
                    snapshot = create_operational_day(
                        session, mapped.operational_day, mapped.config
                    )
                    row = InputVersionRow(
                        id=snapshot.id,
                        operational_date=day,
                        version_number=rows[-1].version_number + 1 if rows else 1,
                        parent_version_id=parent_id,
                        source_snapshot_id=source,
                        idempotency_key=request.idempotency_key,
                        request_hash=fingerprint,
                        reason=request.reason,
                        validation_json=json.dumps(warnings, sort_keys=True),
                    )
                    append_version(session, row)
                    return self.response(session, row)
            except (IntegrityError, OperationalError):
                if attempt:
                    raise InputConflictError(
                        "CONCURRENT_INPUT_REVISION", "Concurrent revision; reload and retry"
                    ) from None
        raise AssertionError("unreachable")

    @staticmethod
    def invalid(code: str, path: str, message: str) -> None:
        raise InputValidationError((ValidationIssue(code, path, message),))
