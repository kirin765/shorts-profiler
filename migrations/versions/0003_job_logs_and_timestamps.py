"""add job logs table and job tracking timestamps."""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0003_job_logs_and_timestamps'
down_revision = '0002_prompt_target_len'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        'jobs',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index('ix_jobs_updated_at', 'jobs', ['updated_at'])

    op.create_table(
        'job_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False, server_default='info'),
        sa.Column('step', sa.String(length=80), nullable=False, server_default='analysis'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_job_logs_job_id_created_at', 'job_logs', ['job_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_job_logs_job_id_created_at', table_name='job_logs')
    op.drop_table('job_logs')

    op.drop_index('ix_jobs_updated_at', table_name='jobs')
    op.drop_column('jobs', 'updated_at')
    op.drop_column('jobs', 'created_at')
