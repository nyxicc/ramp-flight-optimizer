"""Import orchestration over transaction/repository contracts, never an optimizer."""

from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.models import Employee, OperationalDay
from ramp_optimizer.validation import validate_or_raise
from ramp_optimizer_imports.daily_flight_log import DailyFlightLogAdapter
from ramp_optimizer_imports.employee_schedule import TeamWorkAdapter
from ramp_optimizer_imports.enums import TRANSITIONS, ImportStatus, ImportType
from ramp_optimizer_imports.flight_normalization import localize
from ramp_optimizer_imports.models import (
    ImportAdapter,
    ImportError,
    ImportRecord,
    ImportRepository,
    RowCorrection,
)
from ramp_optimizer_imports.safety import UploadLimits, validate_container, validate_filename


class ImportService:
    def __init__(
        self,
        transactions: Callable[[], AbstractContextManager[ImportRepository]],
        *,
        limits: UploadLimits | None = None,
        adapter: ImportAdapter | None = None,
        clock: Callable[[], datetime] | None = None,
        id_provider: Callable[[], str] | None = None,
    ):
        self.transactions = transactions
        self.limits = limits or UploadLimits()
        self.adapter = adapter or TeamWorkAdapter()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_provider = id_provider or (lambda: str(uuid4()))

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Import clock must be timezone aware.")
        return now.astimezone(timezone.utc)

    def upload(
        self,
        content: bytes,
        filename: str,
        media_type: str,
        operational_date: date,
        roster: tuple[Employee, ...],
        config: OptimizerConfig,
        *,
        digest: str | None = None,
        ramp_agents_only: bool = False,
    ) -> ImportRecord:
        filename, media_type = validate_filename(filename, media_type)
        validate_container(content, self.limits)
        validate_or_raise(OperationalDay(operational_date, roster), config)
        import_id = self.id_provider()
        preview = self.adapter.parse(
            content,
            import_id,
            operational_date,
            roster,
            config,
            **({"ramp_agents_only": True} if ramp_agents_only else {}),
        )
        now = self._now()
        record = ImportRecord(
            import_id,
            self.adapter.import_type,
            preview.status,
            filename,
            media_type,
            len(content),
            digest or sha256(content).hexdigest(),
            self.adapter.schema_version,
            now,
            now,
            preview,
        )
        with self.transactions() as repository:
            repository.create(record)
        return record

    def get(self, import_id: str) -> ImportRecord:
        with self.transactions() as repository:
            return repository.get(import_id)

    def get_revision(self, import_id, revision):
        with self.transactions() as repository:
            return repository.get_revision(import_id, revision)

    def upload_flights(
        self, content, filename, media_type, operational_date, policy, config, *, digest=None
    ):
        _, media_type = validate_filename(filename, media_type)
        validate_container(content, self.limits)
        adapter = DailyFlightLogAdapter()
        import_id = self.id_provider()
        preview = adapter.parse(content, import_id, operational_date, policy, config)
        now = self._now()
        record = ImportRecord(
            import_id,
            adapter.import_type,
            preview.status,
            "flight-log.xlsx",
            media_type,
            len(content),
            digest or sha256(content).hexdigest(),
            adapter.schema_version,
            now,
            now,
            preview,
        )
        with self.transactions() as repository:
            repository.create(record)
        return record

    def _adapter(self, record):
        return (
            DailyFlightLogAdapter()
            if record.import_type == ImportType.DAILY_FLIGHT_LOG
            else self.adapter
        )

    def readiness(self, day_id):
        with self.transactions() as repository:
            return repository.readiness(day_id)

    def combine(self, employee_import_id, flight_import_id):
        with self.transactions() as repository:
            # Serialize on a stable parent before querying the composition key.
            employee = repository.claim_confirmed(employee_import_id)
            flight = repository.get(flight_import_id)
            if (
                employee.import_type != ImportType.TEAMWORK_EMPLOYEE_SCHEDULE
                or flight.import_type != ImportType.DAILY_FLIGHT_LOG
                or employee.status != ImportStatus.CONFIRMED
                or flight.status != ImportStatus.CONFIRMED
            ):
                raise ImportError("CONFIRMED_IMPORTS_REQUIRED", 409)
            if (
                employee.preview.operational_date != flight.preview.operational_date
                or employee.preview.config != flight.preview.config
            ):
                raise ImportError("IMPORT_INPUT_MISMATCH", 409)
            existing = repository.composition(employee_import_id, flight_import_id)
            if existing:
                return repository.readiness(existing)
            employee_day = self.adapter.snapshot(employee.preview)
            flight_day = DailyFlightLogAdapter.snapshot(flight.preview)
            zone = ZoneInfo(flight.preview.flight_policy.airport_timezone)
            try:
                shifts = tuple(
                    replace(
                        shift,
                        start=datetime.fromisoformat(localize(shift.start, zone).isoformat()),
                        end=datetime.fromisoformat(localize(shift.end, zone).isoformat()),
                    )
                    for shift in employee_day.employee_shifts
                )
            except ValueError:
                raise ImportError("EMPLOYEE_TIMEZONE_REVIEW_REQUIRED", 409) from None
            day = replace(employee_day, employee_shifts=shifts, flights=flight_day.flights)
            validate_or_raise(day, flight.preview.config)
            day_id = repository.compose(
                employee_import_id, flight_import_id, day, flight.preview.config
            )
            return repository.readiness(day_id)

    def correct(
        self,
        import_id: str,
        revision: int,
        corrections: tuple[RowCorrection, ...],
        *,
        operational_date: date | None = None,
    ) -> ImportRecord:
        with self.transactions() as repository:
            current = repository.claim(import_id, revision)
            self._editable(current)
            adapter = self._adapter(current)
            if operational_date is not None:
                if current.import_type != ImportType.DAILY_FLIGHT_LOG:
                    raise ImportError("INVALID_IMPORT_CORRECTIONS")
                preview = adapter.correct(
                    current.preview, corrections, operational_date=operational_date
                )
            else:
                preview = adapter.correct(current.preview, corrections)
            self._transition(current.status, preview.status)
            record = replace(
                current, preview=preview, status=preview.status, updated_at=self._now()
            )
            repository.revise(record)
            return record

    def confirm(self, import_id: str, revision: int) -> ImportRecord:
        with self.transactions() as repository:
            current = repository.claim(import_id, revision)
            if current.status == ImportStatus.CONFIRMED:
                return current
            adapter = self._adapter(current)
            if current.import_type == ImportType.DAILY_FLIGHT_LOG:
                refreshed = adapter.revalidate(current.preview)
                if refreshed.issues != current.preview.issues and refreshed.confirmation_eligible:
                    # Preserve the original review and append the current import-policy
                    # evaluation before confirming an older saved flight log.
                    refreshed = replace(refreshed, revision=current.preview.revision + 1)
                    self._transition(current.status, refreshed.status)
                    current = replace(current, preview=refreshed, status=refreshed.status,
                                      updated_at=self._now())
                    repository.revise(current)
            if (
                current.status != ImportStatus.READY_TO_CONFIRM
                or not adapter.revalidate(current.preview).confirmation_eligible
            ):
                raise ImportError("IMPORT_NOT_CONFIRMABLE", 409, current.preview.issues)
            day = adapter.snapshot(current.preview)
            validate_or_raise(day, current.preview.config)
            self._transition(current.status, ImportStatus.CONFIRMED)
            day_id = repository.create_day(day, current.preview.config)
            now = self._now()
            record = replace(
                current,
                status=ImportStatus.CONFIRMED,
                confirmed_at=now,
                updated_at=now,
                confirmed_operational_day_id=day_id,
            )
            repository.confirm(record)
            return record

    @staticmethod
    def _editable(record):
        if record.status == ImportStatus.CONFIRMED:
            raise ImportError("IMPORT_ALREADY_CONFIRMED", 409)
        if record.status == ImportStatus.REJECTED:
            raise ImportError("IMPORT_NOT_CONFIRMABLE", 409)

    @staticmethod
    def _transition(before, after):
        if after not in TRANSITIONS[before]:
            raise ImportError("INVALID_IMPORT_TRANSITION", 409)
