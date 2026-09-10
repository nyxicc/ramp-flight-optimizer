"""Enable flight imports while preserving immutable employee revisions."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0003"
down_revision = "20260909_0002"
branch_labels = None
depends_on = None


def _constraint(expression):
    # SQLite batch replacement must not cascade-delete the revision history.
    # Copy into a constraint-free temporary table before rebuilding the parent.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            "CREATE TEMPORARY TABLE saved_import_revisions AS SELECT * FROM import_revisions"
        )
        op.drop_table("import_revisions")
    with op.batch_alter_table("import_jobs") as batch:
        batch.drop_constraint("ck_import_type", type_="check")
        batch.create_check_constraint("ck_import_type", expression)
    if bind.dialect.name == "sqlite":
        op.create_table(
            "import_revisions",
            sa.Column(
                "import_id",
                sa.String(36),
                sa.ForeignKey("import_jobs.import_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("revision", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("preview_json", sa.Text(), nullable=False),
            sa.Column("preview_hash", sa.String(64), nullable=False),
            sa.CheckConstraint("revision >= 1", name="ck_review_revision"),
        )
        op.execute("INSERT INTO import_revisions SELECT * FROM saved_import_revisions")
        op.execute("DROP TABLE saved_import_revisions")


def upgrade():
    _constraint("import_type IN ('TEAMWORK_EMPLOYEE_SCHEDULE','DAILY_FLIGHT_LOG')")
    op.create_table(
        "import_compositions",
        sa.Column(
            "operational_day_id",
            sa.String(36),
            sa.ForeignKey("operational_days.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "employee_import_id",
            sa.String(36),
            sa.ForeignKey("import_jobs.import_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "flight_import_id",
            sa.String(36),
            sa.ForeignKey("import_jobs.import_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint("employee_import_id", "flight_import_id", name="uq_import_composition"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM import_jobs WHERE import_type = 'DAILY_FLIGHT_LOG'")
    ):
        raise RuntimeError(
            "Cannot downgrade while flight import history exists; preserve or archive the database first."
        )
    op.drop_table("import_compositions")
    _constraint("import_type = 'TEAMWORK_EMPLOYEE_SCHEDULE'")
