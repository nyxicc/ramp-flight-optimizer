"""Import orchestration over transaction/repository contracts, never an optimizer."""

from dataclasses import replace
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Callable
from uuid import uuid4

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.models import Employee, OperationalDay
from ramp_optimizer.validation import validate_or_raise
from ramp_optimizer_imports.employee_schedule import TeamWorkAdapter
from ramp_optimizer_imports.enums import ImportStatus, TRANSITIONS
from ramp_optimizer_imports.models import ImportAdapter, ImportError, ImportRecord, ImportRepository, RowCorrection
from ramp_optimizer_imports.safety import UploadLimits, validate_container, validate_filename


class ImportService:
    def __init__(
        self, transactions: Callable[[], AbstractContextManager[ImportRepository]], *,
        limits: UploadLimits | None = None, adapter: ImportAdapter | None = None,
        clock: Callable[[], datetime] | None = None, id_provider: Callable[[], str] | None = None,
    ):
        self.transactions = transactions
        self.limits = limits or UploadLimits()
        self.adapter = adapter or TeamWorkAdapter()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_provider = id_provider or (lambda: str(uuid4()))

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError('Import clock must be timezone aware.')
        return now.astimezone(timezone.utc)

    def upload(
        self, content: bytes, filename: str, media_type: str, operational_date: date,
        roster: tuple[Employee, ...], config: OptimizerConfig, *, digest: str | None = None,
    ) -> ImportRecord:
        filename, media_type = validate_filename(filename, media_type)
        validate_container(content, self.limits)
        validate_or_raise(OperationalDay(operational_date, roster), config)
        import_id = self.id_provider()
        preview = self.adapter.parse(content, import_id, operational_date, roster, config)
        now = self._now()
        record = ImportRecord(
            import_id, self.adapter.import_type, preview.status, filename, media_type, len(content),
            digest or sha256(content).hexdigest(), self.adapter.schema_version, now, now, preview,
        )
        with self.transactions() as repository:
            repository.create(record)
        return record

    def get(self, import_id: str) -> ImportRecord:
        with self.transactions() as repository:
            return repository.get(import_id)

    def correct(self, import_id: str, revision: int, corrections: tuple[RowCorrection, ...]) -> ImportRecord:
        with self.transactions() as repository:
            current = repository.claim(import_id, revision)
            self._editable(current)
            preview = self.adapter.correct(current.preview, corrections)
            self._transition(current.status, preview.status)
            record = replace(current, preview=preview, status=preview.status, updated_at=self._now())
            repository.revise(record)
            return record

    def confirm(self, import_id: str, revision: int) -> ImportRecord:
        with self.transactions() as repository:
            current = repository.claim(import_id, revision)
            if current.status == ImportStatus.CONFIRMED:
                return current
            if current.status != ImportStatus.READY_TO_CONFIRM or not self.adapter.revalidate(current.preview).confirmation_eligible:
                raise ImportError('IMPORT_NOT_CONFIRMABLE', 409, current.preview.issues)
            day = self.adapter.snapshot(current.preview)
            validate_or_raise(day, current.preview.config)
            self._transition(current.status, ImportStatus.CONFIRMED)
            day_id = repository.create_day(day, current.preview.config)
            now = self._now()
            record = replace(current, status=ImportStatus.CONFIRMED, confirmed_at=now,
                             updated_at=now, confirmed_operational_day_id=day_id)
            repository.confirm(record)
            return record

    @staticmethod
    def _editable(record):
        if record.status == ImportStatus.CONFIRMED:
            raise ImportError('IMPORT_ALREADY_CONFIRMED', 409)
        if record.status == ImportStatus.REJECTED:
            raise ImportError('IMPORT_NOT_CONFIRMABLE', 409)

    @staticmethod
    def _transition(before, after):
        if after not in TRANSITIONS[before]:
            raise ImportError('INVALID_IMPORT_TRANSITION', 409)
