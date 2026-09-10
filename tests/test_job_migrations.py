"""Job migration preserves earlier fictional versions and runs."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text

from ramp_optimizer_api.input_schemas import DraftRequest
from ramp_optimizer_api.input_services import InputService
from ramp_optimizer_api.job_services import JobService
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.models import Base
from tests.import_fixtures import DAY


def test_job_upgrade_downgrade_and_history_guard(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}"
    monkeypatch.setenv("RAMP_OPTIMIZER_DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "20260910_0004")
    engine = create_database_engine(url)
    sessions = make_session_factory(engine)
    inputs = InputService(sessions)
    before = inputs.create(DAY, DraftRequest(idempotency_key="fictional-source"))
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    command.downgrade(cfg, "20260910_0004")
    assert inputs.get(str(before.id)) == before
    command.upgrade(cfg, "head")
    service = JobService(sessions)
    job = service.submit(source_id=str(before.id))
    with pytest.raises(RuntimeError, match="job history"):
        command.downgrade(cfg, "20260910_0004")
    assert service.get(str(job.id)) == job
    assert inputs.get(str(before.id)) == before
    engine.dispose()
