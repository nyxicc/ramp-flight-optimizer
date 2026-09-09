"""Alembic migration lifecycle test against an empty SQLite database."""

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect


def test_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite"
    environment = os.environ.copy()
    environment["RAMP_OPTIMIZER_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"

    def alembic(*arguments: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", *arguments], check=True, env=environment)

    expected = {"operational_days", "operational_day_employees", "operational_day_shifts", "operational_day_flights", "operational_day_fixed_assignments", "optimization_runs"}
    alembic("upgrade", "head")
    engine = create_engine(environment["RAMP_OPTIMIZER_DATABASE_URL"])
    assert expected <= set(inspect(engine).get_table_names())
    assert {item["name"] for item in inspect(engine).get_indexes("operational_days")} >= {"ix_operational_days_operational_date", "ix_operational_days_created_at_utc", "ix_operational_days_input_hash"}
    engine.dispose()

    alembic("downgrade", "base")
    engine = create_engine(environment["RAMP_OPTIMIZER_DATABASE_URL"])
    assert not expected & set(inspect(engine).get_table_names())
    engine.dispose()
    alembic("upgrade", "head")
