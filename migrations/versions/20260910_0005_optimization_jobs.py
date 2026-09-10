"""Local background optimization queue, claims, and durable diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0005"
down_revision = "20260910_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "optimization_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "operational_day_id",
            sa.String(36),
            sa.ForeignKey("operational_days.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_version_id",
            sa.String(36),
            sa.ForeignKey("input_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("active_key", sa.String(64), unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32)),
        sa.Column("finished_at", sa.String(32)),
        sa.Column("worker_token", sa.String(36)),
        sa.Column("progress_json", sa.Text(), nullable=False),
        sa.Column("checkpoint_json", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "result_run_id",
            sa.String(36),
            sa.ForeignKey("optimization_runs.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','CANCELLING','SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')",
            name="ck_job_status",
        ),
        sa.CheckConstraint(
            "timeout_seconds > 0 AND timeout_seconds <= 3600", name="ck_job_timeout"
        ),
    )
    op.create_index("ix_jobs_queue", "optimization_jobs", ["status", "created_at", "id"])
    op.create_table(
        "optimization_job_requests",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("optimization_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM optimization_jobs")):
        raise RuntimeError("Cannot downgrade while optimization job history exists.")
    op.drop_table("optimization_job_requests")
    op.drop_index("ix_jobs_queue", table_name="optimization_jobs")
    op.drop_table("optimization_jobs")
