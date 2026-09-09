"""Add reviewed import jobs and immutable preview revisions.

Revision ID: 20260909_0002
Revises: 20260908_0001
"""

from alembic import op
import sqlalchemy as sa

revision = '20260909_0002'
down_revision = '20260908_0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'import_jobs',
        sa.Column('import_id', sa.String(36), primary_key=True),
        sa.Column('import_type', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('original_filename', sa.String(255), nullable=False),
        sa.Column('media_type', sa.String(128), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(32), nullable=False),
        sa.Column('updated_at', sa.String(32), nullable=False),
        sa.Column('confirmed_at', sa.String(32)),
        sa.Column('confirmed_operational_day_id', sa.String(36), sa.ForeignKey('operational_days.id', ondelete='RESTRICT'), unique=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in (
            'fatal_count', 'error_count', 'warning_count', 'accepted_shift_count', 'vacancy_count', 'unresolved_row_count')],
        sa.CheckConstraint("status IN ('REVIEW_REQUIRED','READY_TO_CONFIRM','REJECTED','CONFIRMED')", name='ck_import_status'),
        sa.CheckConstraint("import_type = 'TEAMWORK_EMPLOYEE_SCHEDULE'", name='ck_import_type'),
        sa.CheckConstraint('revision >= 1', name='ck_import_revision'),
        sa.CheckConstraint("(status = 'CONFIRMED' AND confirmed_operational_day_id IS NOT NULL AND confirmed_at IS NOT NULL) OR (status != 'CONFIRMED' AND confirmed_operational_day_id IS NULL AND confirmed_at IS NULL)", name='ck_import_confirmation'),
    )
    op.create_index('ix_import_jobs_sha256', 'import_jobs', ['sha256'])
    op.create_index('ix_import_jobs_error_count', 'import_jobs', ['error_count'])
    op.create_table(
        'import_revisions',
        sa.Column('import_id', sa.String(36), sa.ForeignKey('import_jobs.import_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.String(32), nullable=False),
        sa.Column('preview_json', sa.Text(), nullable=False),
        sa.Column('preview_hash', sa.String(64), nullable=False),
        sa.CheckConstraint('revision >= 1', name='ck_review_revision'),
    )


def downgrade():
    op.drop_table('import_revisions')
    op.drop_index('ix_import_jobs_error_count', table_name='import_jobs')
    op.drop_index('ix_import_jobs_sha256', table_name='import_jobs')
    op.drop_table('import_jobs')
