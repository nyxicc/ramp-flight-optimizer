"""Milestone 19 upgrade/downgrade preserves fictional prior snapshots."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from ramp_optimizer import Employee, OperationalDay, OptimizerConfig
from ramp_optimizer_api.input_schemas import DraftRequest
from ramp_optimizer_api.input_services import InputService
from ramp_optimizer_imports.services import ImportService
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.imports import import_transactions
from ramp_optimizer_persistence.models import Base
from ramp_optimizer_persistence.repositories import create_operational_day, get_operational_day
from tests.import_fixtures import DAY, workbook
from tests.test_flight_import import DAY as FLIGHT_DAY
from tests.test_flight_import import POLICY
from tests.test_flight_import import workbook as flight_workbook


def test_upgrade_downgrade_preserves_data(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}"
    monkeypatch.setenv("RAMP_OPTIMIZER_DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "20260909_0003")
    engine = create_database_engine(url)
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        before = create_operational_day(session, OperationalDay(DAY), OptimizerConfig())
    service = ImportService(import_transactions(sessions))
    imported = service.upload(
        workbook(),
        "fictional.xlsx",
        "application/octet-stream",
        DAY,
        (Employee("SYN001", "Fictional Avery"),),
        OptimizerConfig(),
    )
    confirmed = service.confirm(imported.import_id, 1)
    flight_import = service.upload_flights(
        flight_workbook(),
        "fictional-flights.xlsx",
        "application/octet-stream",
        FLIGHT_DAY,
        POLICY,
        OptimizerConfig(),
    )
    confirmed_flights = service.confirm(flight_import.import_id, 1)
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(cfg, "20260909_0003")
    assert service.get(imported.import_id) == confirmed
    assert service.get(flight_import.import_id) == confirmed_flights
    with sessions() as session:
        assert get_operational_day(session, before.id) == before
    command.upgrade(cfg, "head")
    version = InputService(sessions).create(DAY, DraftRequest(idempotency_key="history"))
    with pytest.raises(RuntimeError, match="input version history"):
        command.downgrade(cfg, "20260909_0003")
    assert InputService(sessions).get(str(version.id)) == version
    engine.dispose()
