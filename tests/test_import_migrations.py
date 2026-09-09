"""Real Milestone 17 -> 18A -> 17 migrations, schema and relational integrity."""

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from ramp_optimizer import Employee, OperationalDay, OptimizerConfig
from ramp_optimizer_imports.models import RowCorrection
from ramp_optimizer_imports.services import ImportService
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.imports import import_transactions
from ramp_optimizer_persistence.models import Base, ImportJobRow, ImportRevisionRow
from ramp_optimizer_persistence.repositories import create_operational_day, get_operational_day
from tests.import_fixtures import DAY, workbook


def test_migration_lifecycle_schema_constraints_and_preserved_days(tmp_path, monkeypatch):
    url = f'sqlite:///{(tmp_path / "migrations.sqlite").as_posix()}'
    monkeypatch.setenv('RAMP_OPTIMIZER_DATABASE_URL', url)
    cfg = Config('alembic.ini')
    command.upgrade(cfg, '20260908_0001')
    engine = create_database_engine(url)
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        before = create_operational_day(session, OperationalDay(DAY), OptimizerConfig())
    assert 'import_jobs' not in inspect(engine).get_table_names()
    command.upgrade(cfg, 'head')
    inspector = inspect(engine)
    assert {'import_jobs', 'import_revisions'} <= set(inspector.get_table_names())
    assert inspector.get_pk_constraint('import_revisions')['constrained_columns'] == ['import_id', 'revision']
    assert inspector.get_foreign_keys('import_revisions')[0]['referred_table'] == 'import_jobs'
    assert inspector.get_foreign_keys('import_jobs')[0]['referred_table'] == 'operational_days'
    assert any(c['column_names'] == ['confirmed_operational_day_id'] for c in inspector.get_unique_constraints('import_jobs'))
    with engine.connect() as connection:
        assert connection.scalar(text('PRAGMA foreign_keys')) == 1
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    service = ImportService(import_transactions(sessions))
    imported = service.upload(workbook(), 'fictional.xlsx', 'application/octet-stream', DAY,
                              (Employee('SYN001','Fictional Avery'),), OptimizerConfig())
    for sql in (
        "UPDATE import_jobs SET status='INVALID'",
        "UPDATE import_jobs SET status='CONFIRMED'",
        "UPDATE import_jobs SET revision=0",
        "UPDATE import_jobs SET import_type='GUESSED_FLIGHT_LOG'",
        "UPDATE import_revisions SET import_id='unknown'",
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(sql))
    confirmed = service.confirm(imported.import_id, 1)
    other = service.upload(workbook(), 'fictional.xlsx', 'application/octet-stream', DAY,
                           (Employee('SYN001','Fictional Avery'),), OptimizerConfig())
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            row = session.get(ImportJobRow, other.import_id)
            row.status = 'CONFIRMED'
            row.confirmed_at = confirmed.confirmed_at.isoformat()
            row.confirmed_operational_day_id = confirmed.confirmed_operational_day_id
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text('DELETE FROM operational_days WHERE id=:id'), {'id':confirmed.confirmed_operational_day_id})
    command.downgrade(cfg, '20260908_0001')
    assert 'import_jobs' not in inspect(engine).get_table_names()
    with sessions() as session:
        assert get_operational_day(session, before.id) == before
        assert get_operational_day(session, confirmed.confirmed_operational_day_id).day.flights == ()
    command.downgrade(cfg, 'base')
    assert set(inspect(engine).get_table_names()) <= {'alembic_version'}
    command.upgrade(cfg, 'head')
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.execute(text('PRAGMA foreign_key_check')).all() == []
    engine.dispose()
