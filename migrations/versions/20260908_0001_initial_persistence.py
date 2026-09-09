"""Create immutable operational-day snapshots and optimization results.

Revision ID: 20260908_0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_days",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("created_at_utc", sa.String(length=32), nullable=False),
        sa.Column("input_schema_version", sa.Integer(), nullable=False),
        sa.Column("optimizer_config_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("employee_count", sa.Integer(), nullable=False),
        sa.Column("shift_count", sa.Integer(), nullable=False),
        sa.Column("flight_count", sa.Integer(), nullable=False),
        sa.Column("fixed_assignment_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operational_days_operational_date",
        "operational_days",
        ["operational_date"],
    )
    op.create_index(
        "ix_operational_days_created_at_utc",
        "operational_days",
        ["created_at_utc"],
    )
    op.create_index(
        "ix_operational_days_input_hash", "operational_days", ["input_hash"]
    )

    op.create_table(
        "operational_day_employees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operational_day_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("qualifications_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operational_day_id"], ["operational_days.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_employees_ordinal",
        ),
    )
    op.create_table(
        "operational_day_shifts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operational_day_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.String(length=255), nullable=False),
        sa.Column("start_iso", sa.String(length=64), nullable=False),
        sa.Column("end_iso", sa.String(length=64), nullable=False),
        sa.Column("normalized_role", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["operational_day_id"], ["operational_days.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_shifts_ordinal",
        ),
    )
    op.create_table(
        "operational_day_flights",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operational_day_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("arrival_flight_number", sa.String(length=255), nullable=True),
        sa.Column("arrival_time_iso", sa.String(length=64), nullable=True),
        sa.Column("departure_flight_number", sa.String(length=255), nullable=True),
        sa.Column("departure_time_iso", sa.String(length=64), nullable=True),
        sa.Column("gate", sa.String(length=255), nullable=True),
        sa.Column("heavy", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operational_day_id"], ["operational_days.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_flights_ordinal",
        ),
    )
    op.create_table(
        "operational_day_fixed_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operational_day_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.String(length=255), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["flight_id"], ["operational_day_flights.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operational_day_id"], ["operational_days.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_day_id",
            "employee_id",
            "flight_id",
            name="uq_operational_day_fixed_assignment",
        ),
        sa.UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_fixed_assignments_ordinal",
        ),
    )
    op.create_table(
        "optimization_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("operational_day_id", sa.String(length=36), nullable=False),
        sa.Column("created_at_utc", sa.String(length=32), nullable=False),
        sa.Column("package_version", sa.String(length=64), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=False),
        sa.Column("result_schema_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("solver_status", sa.String(length=64), nullable=False),
        sa.Column("operational_readiness", sa.String(length=64), nullable=False),
        sa.Column("emergency_pass_disposition", sa.String(length=96), nullable=False),
        sa.Column("solver_runtime_seconds", sa.Float(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("objective_stage_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operational_day_id"], ["operational_days.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_optimization_runs_operational_day_id",
        "optimization_runs",
        ["operational_day_id"],
    )
    op.create_index(
        "ix_optimization_runs_created_at_utc",
        "optimization_runs",
        ["created_at_utc"],
    )
    op.create_index(
        "ix_optimization_runs_solver_status",
        "optimization_runs",
        ["solver_status"],
    )
    op.create_index(
        "ix_optimization_runs_operational_readiness",
        "optimization_runs",
        ["operational_readiness"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_optimization_runs_operational_readiness", table_name="optimization_runs"
    )
    op.drop_index("ix_optimization_runs_solver_status", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_created_at_utc", table_name="optimization_runs")
    op.drop_index(
        "ix_optimization_runs_operational_day_id", table_name="optimization_runs"
    )
    op.drop_table("optimization_runs")
    op.drop_table("operational_day_fixed_assignments")
    op.drop_table("operational_day_flights")
    op.drop_table("operational_day_shifts")
    op.drop_table("operational_day_employees")
    op.drop_index("ix_operational_days_input_hash", table_name="operational_days")
    op.drop_index("ix_operational_days_created_at_utc", table_name="operational_days")
    op.drop_index("ix_operational_days_operational_date", table_name="operational_days")
    op.drop_table("operational_days")
