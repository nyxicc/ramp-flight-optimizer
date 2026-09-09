"""Repository, serialization, and database-adapter acceptance tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ramp_optimizer import EmployeeShift
from ramp_optimizer_api.mapping import optimization_result_to_response
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.errors import PersistenceIntegrityError, ResourceNotFoundError
from ramp_optimizer_persistence.models import Base, EmployeeRow, OperationalDayRow
from ramp_optimizer_persistence.repositories import (
    create_operational_day,
    create_optimization_run,
    get_operational_day,
    get_optimization_run,
    list_operational_days,
    list_optimization_runs_for_day,
)
from ramp_optimizer_persistence.serialization import canonical_input_hash
from ramp_optimizer_persistence.settings import DatabaseConfigurationError, DatabaseSettings
from tests.scenario_builders import small_ready_scenario
from ramp_optimizer import optimize_flight_assignments


@pytest.fixture
def sessions():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def test_database_settings_and_sqlite_foreign_keys() -> None:
    assert DatabaseSettings.from_environment({}).database_url == "sqlite:///./ramp_optimizer.db"
    assert DatabaseSettings.from_environment({"RAMP_OPTIMIZER_DATABASE_URL": "sqlite://"}).database_url == "sqlite://"
    with pytest.raises(DatabaseConfigurationError, match="Database URL is invalid") as caught:
        DatabaseSettings("not a valid url secret-password")
    assert "secret-password" not in str(caught.value)

    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    engine.dispose()


def test_operational_day_round_trip_order_datetime_config_and_hash(sessions) -> None:
    scenario = small_ready_scenario()
    offset = timezone(timedelta(hours=-5))
    shifts = tuple(
        EmployeeShift(item.employee_id, item.start.replace(tzinfo=offset), item.end.replace(tzinfo=offset), item.normalized_role)
        for item in scenario.day.employee_shifts
    )
    day = replace(scenario.day, employee_shifts=shifts)
    with sessions.begin() as session:
        created = create_operational_day(session, day, scenario.config, id_provider=lambda: "00000000-0000-0000-0000-000000000001")
    with sessions() as session:
        loaded = get_operational_day(session, created.id)

    assert loaded.day == day
    assert loaded.config == scenario.config
    assert loaded.input_hash == canonical_input_hash(day, scenario.config)
    assert loaded.day.employee_shifts[0].start.utcoffset() == timedelta(hours=-5)
    assert [item.employee_id for item in loaded.day.employees] == [item.employee_id for item in day.employees]


def test_naive_datetime_and_multiple_same_date_snapshots(sessions) -> None:
    scenario = small_ready_scenario()
    ids = iter(["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"])
    with sessions.begin() as session:
        first = create_operational_day(session, scenario.day, scenario.config, id_provider=lambda: next(ids))
        create_operational_day(session, scenario.day, scenario.config, id_provider=lambda: next(ids))
    with sessions() as session:
        loaded = get_operational_day(session, first.id)
        items, total = list_operational_days(session, limit=1, offset=1)
    stored_time = loaded.day.flights[0].arrival_time or loaded.day.flights[0].departure_time
    assert stored_time is not None and stored_time.tzinfo is None
    assert total == 2
    assert len(items) == 1


def test_unknown_tampered_and_atomic_rollback(sessions) -> None:
    scenario = small_ready_scenario()
    with sessions() as session:
        with pytest.raises(ResourceNotFoundError):
            get_operational_day(session, "00000000-0000-0000-0000-000000000099")

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            create_operational_day(session, scenario.day, scenario.config, id_provider=lambda: "00000000-0000-0000-0000-000000000001")
            session.add(EmployeeRow(operational_day_id="00000000-0000-0000-0000-000000000001", ordinal=0, employee_id="duplicate", name="duplicate", enabled=True, qualifications_json="[]"))
    with sessions() as session:
        assert session.scalar(select(OperationalDayRow)) is None

    with sessions.begin() as session:
        created = create_operational_day(session, scenario.day, scenario.config)
        created_id = created.id
    with sessions.begin() as session:
        session.get(OperationalDayRow, created_id).input_hash = "0" * 64
    with sessions() as session, pytest.raises(PersistenceIntegrityError):
        get_operational_day(session, created_id)


def test_complete_optimization_run_round_trip_and_isolation(sessions) -> None:
    scenario = small_ready_scenario()
    result = optimization_result_to_response(optimize_flight_assignments(scenario.day, scenario.config)).model_dump(mode="json")
    with sessions.begin() as session:
        day = create_operational_day(session, scenario.day, scenario.config)
    with sessions.begin() as session:
        first = create_optimization_run(session, day, result, package_version="0.1.0", api_version="1")
        second = create_optimization_run(session, day, result, package_version="0.1.0", api_version="1")
    with sessions() as session:
        loaded = get_optimization_run(session, first.id)
        items, total = list_optimization_runs_for_day(session, day.id, limit=1, offset=0)
    assert loaded.result == result
    assert loaded.input_hash == day.input_hash
    assert loaded.objective_stage_count == len(result["objective_values"])
    assert loaded.attempt_count == len(result["attempts"])
    assert total == 2 and items[0].id in {first.id, second.id}
