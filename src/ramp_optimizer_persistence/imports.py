"""Transaction-scoped import repository with serialized revision claims."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from hashlib import sha256

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from ramp_optimizer_imports.enums import ImportStatus, ImportType
from ramp_optimizer_imports.models import ImportError, ImportRecord
from ramp_optimizer_imports.serialization import canonical_json, load_preview, preview_counts
from ramp_optimizer_persistence.errors import (
    DatabaseOperationError,
    PersistenceIntegrityError,
    ResourceNotFoundError,
)
from ramp_optimizer_persistence.models import ImportCompositionRow, ImportJobRow, ImportRevisionRow
from ramp_optimizer_persistence.repositories import create_operational_day, get_operational_day


def is_imported_snapshot(session, resource_id: str) -> bool:
    return (
        session.get(ImportCompositionRow, resource_id) is not None
        or session.scalar(
            select(ImportJobRow.import_id).where(
                ImportJobRow.confirmed_operational_day_id == resource_id
            )
        )
        is not None
    )


def import_transactions(session_factory):
    @contextmanager
    def transaction():
        try:
            with session_factory.begin() as session:
                yield SQLImportRepository(session)
        except SQLAlchemyError:
            raise DatabaseOperationError("Import database operation failed.") from None

    return transaction


class SQLImportRepository:
    def __init__(self, session):
        self.session = session

    def get(self, import_id):
        row = self.session.get(ImportJobRow, import_id)
        if row is None:
            raise ResourceNotFoundError("Import was not found.")
        revision = self.session.get(ImportRevisionRow, (import_id, row.revision))
        if (
            revision is None
            or sha256(revision.preview_json.encode()).hexdigest() != revision.preview_hash
        ):
            raise PersistenceIntegrityError("Import review integrity check failed.")
        try:
            preview = load_preview(revision.preview_json)
            if preview.revision != row.revision or any(
                getattr(row, k) != v for k, v in preview_counts(preview).items()
            ):
                raise ValueError()
            status = ImportStatus(row.status)
            if status != ImportStatus.CONFIRMED and status != preview.status:
                raise ValueError()
            return ImportRecord(
                row.import_id,
                ImportType(row.import_type),
                status,
                row.original_filename,
                row.media_type,
                row.byte_size,
                row.sha256,
                row.schema_version,
                datetime.fromisoformat(row.created_at),
                datetime.fromisoformat(row.updated_at),
                preview,
                datetime.fromisoformat(row.confirmed_at) if row.confirmed_at else None,
                row.confirmed_operational_day_id,
            )
        except (ValueError, TypeError, KeyError):
            raise PersistenceIntegrityError("Import review integrity check failed.") from None

    def claim(self, import_id, revision):
        # First SQL statement is a conditional write: serializes SQLite writers and
        # takes a row lock on databases supporting concurrent writers. No read/modify race.
        result = self.session.execute(
            update(ImportJobRow)
            .where(
                ImportJobRow.import_id == import_id,
                ImportJobRow.revision == revision,
            )
            .values(revision=ImportJobRow.revision)
        )
        if result.rowcount != 1:
            self.get(import_id)  # distinguish unknown ID from stale revision
            raise ImportError("IMPORT_REVISION_CONFLICT", 409)
        return self.get(import_id)

    def claim_confirmed(self, import_id):
        # First statement takes the same write lock used by revision claims.
        self.session.execute(
            update(ImportJobRow)
            .where(ImportJobRow.import_id == import_id)
            .values(revision=ImportJobRow.revision)
        )
        return self.get(import_id)

    def get_revision(self, import_id, revision):
        current = self.get(import_id)
        if current.preview.revision == revision:
            return current
        stored = self.session.get(ImportRevisionRow, (import_id, revision))
        if stored is None:
            raise ResourceNotFoundError("Import revision was not found.")
        if sha256(stored.preview_json.encode()).hexdigest() != stored.preview_hash:
            raise PersistenceIntegrityError("Import review integrity check failed.")
        try:
            preview = load_preview(stored.preview_json)
        except (ValueError, TypeError, KeyError):
            raise PersistenceIntegrityError("Import review integrity check failed.") from None
        return replace(
            current,
            preview=preview,
            status=preview.status,
            updated_at=datetime.fromisoformat(stored.created_at),
            confirmed_at=None,
            confirmed_operational_day_id=None,
        )

    def create(self, record):
        row = ImportJobRow(
            import_id=record.import_id,
            import_type=record.import_type,
            status=record.status,
            original_filename=record.original_filename,
            media_type=record.media_type,
            byte_size=record.byte_size,
            sha256=record.sha256,
            schema_version=record.schema_version,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
            revision=record.preview.revision,
            **preview_counts(record.preview),
        )
        self.session.add(row)
        self.session.flush()
        self._revision(record)

    def _revision(self, record):
        payload = canonical_json(record.preview)
        self.session.add(
            ImportRevisionRow(
                import_id=record.import_id,
                revision=record.preview.revision,
                created_at=record.updated_at.isoformat(),
                preview_json=payload,
                preview_hash=sha256(payload.encode()).hexdigest(),
            )
        )
        self.session.flush()

    def revise(self, record):
        self._revision(record)
        row = self.session.get(ImportJobRow, record.import_id)
        row.revision = record.preview.revision
        row.status = record.status
        row.updated_at = record.updated_at.isoformat()
        for name, count in preview_counts(record.preview).items():
            setattr(row, name, count)

    def confirm(self, record):
        row = self.session.get(ImportJobRow, record.import_id)
        row.status = record.status
        row.confirmed_at = record.confirmed_at.isoformat()
        row.updated_at = record.updated_at.isoformat()
        row.confirmed_operational_day_id = record.confirmed_operational_day_id

    def create_day(self, day, config):
        return create_operational_day(self.session, day, config).id

    def composition(self, employee_id, flight_id):
        return self.session.scalar(
            select(ImportCompositionRow.operational_day_id).where(
                ImportCompositionRow.employee_import_id == employee_id,
                ImportCompositionRow.flight_import_id == flight_id,
            )
        )

    def compose(self, employee_id, flight_id, day, config):
        day_id = self.create_day(day, config)
        self.session.add(
            ImportCompositionRow(
                operational_day_id=day_id,
                employee_import_id=employee_id,
                flight_import_id=flight_id,
            )
        )
        self.session.flush()
        return day_id

    def readiness(self, day_id):
        snapshot = get_operational_day(self.session, day_id)
        composition = self.session.get(ImportCompositionRow, day_id)
        types = set(
            self.session.scalars(
                select(ImportJobRow.import_type).where(
                    ImportJobRow.confirmed_operational_day_id == day_id
                )
            )
        )
        employee = composition is not None or ImportType.TEAMWORK_EMPLOYEE_SCHEDULE in types
        flight = composition is not None or ImportType.DAILY_FLIGHT_LOG in types
        return {
            "operational_day_id": day_id,
            "confirmed_employee_schedule": employee,
            "confirmed_flight_log": flight,
            "both_inputs_confirmed": employee and flight,
            "unresolved_blocking_issues": False,
            "optimization_eligible": employee
            and flight
            and bool(snapshot.day.employee_shifts)
            and bool(snapshot.day.flights),
        }
