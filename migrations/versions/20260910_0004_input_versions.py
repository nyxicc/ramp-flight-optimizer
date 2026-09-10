"""Immutable input version lineage.

Revision ID: 20260910_0004
Revises: 20260909_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0004"
down_revision = "20260909_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "input_versions",
        sa.Column(
            "id",
            sa.String(36),
            sa.ForeignKey("operational_days.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_version_id",
            sa.String(36),
            sa.ForeignKey("input_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "source_snapshot_id",
            sa.String(36),
            sa.ForeignKey("operational_days.id", ondelete="RESTRICT"),
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("operational_date", "version_number", name="uq_input_version_number"),
        sa.UniqueConstraint("operational_date", "idempotency_key", name="uq_input_version_key"),
        sa.CheckConstraint("version_number >= 1", name="ck_input_version_number"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM input_versions")):
        raise RuntimeError(
            "Cannot downgrade while input version history exists; preserve the database first."
        )
    op.drop_table("input_versions")
