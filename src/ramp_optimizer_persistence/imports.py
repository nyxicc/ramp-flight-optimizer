"""Transaction-scoped import repository with serialized revision claims."""

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from ramp_optimizer_imports.enums import ImportStatus, ImportType
from ramp_optimizer_imports.models import ImportError, ImportRecord
from ramp_optimizer_imports.serialization import canonical_json, load_preview, preview_counts
from ramp_optimizer_persistence.errors import DatabaseOperationError, PersistenceIntegrityError, ResourceNotFoundError
from ramp_optimizer_persistence.models import ImportJobRow, ImportRevisionRow
from ramp_optimizer_persistence.repositories import create_operational_day


def is_imported_snapshot(session, resource_id: str) -> bool:
    return session.scalar(select(ImportJobRow.import_id).where(
        ImportJobRow.confirmed_operational_day_id == resource_id)) is not None


def import_transactions(session_factory):
    @contextmanager
    def transaction():
        try:
            with session_factory.begin() as session:
                yield SQLImportRepository(session)
        except SQLAlchemyError:
            raise DatabaseOperationError('Import database operation failed.') from None
    return transaction


class SQLImportRepository:
    def __init__(self, session):
        self.session = session

    def get(self, import_id):
        row = self.session.get(ImportJobRow, import_id)
        if row is None:
            raise ResourceNotFoundError('Import was not found.')
        revision = self.session.get(ImportRevisionRow, (import_id, row.revision))
        if revision is None or sha256(revision.preview_json.encode()).hexdigest() != revision.preview_hash:
            raise PersistenceIntegrityError('Import review integrity check failed.')
        try:
            preview = load_preview(revision.preview_json)
            if preview.revision != row.revision or any(getattr(row, k) != v for k, v in preview_counts(preview).items()):
                raise ValueError()
            status = ImportStatus(row.status)
            if status != ImportStatus.CONFIRMED and status != preview.status:
                raise ValueError()
            return ImportRecord(
                row.import_id, ImportType(row.import_type), status, row.original_filename,
                row.media_type, row.byte_size, row.sha256, row.schema_version,
                datetime.fromisoformat(row.created_at), datetime.fromisoformat(row.updated_at), preview,
                datetime.fromisoformat(row.confirmed_at) if row.confirmed_at else None,
                row.confirmed_operational_day_id,
            )
        except (ValueError, TypeError, KeyError):
            raise PersistenceIntegrityError('Import review integrity check failed.') from None

    def claim(self, import_id, revision):
        # First SQL statement is a conditional write: serializes SQLite writers and
        # takes a row lock on databases supporting concurrent writers. No read/modify race.
        result = self.session.execute(update(ImportJobRow).where(
            ImportJobRow.import_id == import_id, ImportJobRow.revision == revision,
        ).values(revision=ImportJobRow.revision))
        if result.rowcount != 1:
            self.get(import_id)  # distinguish unknown ID from stale revision
            raise ImportError('IMPORT_REVISION_CONFLICT', 409)
        return self.get(import_id)

    def create(self, record):
        row = ImportJobRow(
            import_id=record.import_id, import_type=record.import_type, status=record.status,
            original_filename=record.original_filename, media_type=record.media_type,
            byte_size=record.byte_size, sha256=record.sha256, schema_version=record.schema_version,
            created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
            revision=record.preview.revision, **preview_counts(record.preview),
        )
        self.session.add(row)
        self.session.flush()
        self._revision(record)

    def _revision(self, record):
        payload = canonical_json(record.preview)
        self.session.add(ImportRevisionRow(
            import_id=record.import_id, revision=record.preview.revision,
            created_at=record.updated_at.isoformat(), preview_json=payload,
            preview_hash=sha256(payload.encode()).hexdigest(),
        ))
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
