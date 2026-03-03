"""initial tables for shorts profiler."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'videos',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('duration_sec', sa.Float(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('category_tag', sa.String(length=80), nullable=True),
        sa.Column('source_type', sa.String(length=20), nullable=False),
        sa.Column('source_ref', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Index('ix_videos_created_at', 'created_at'),
        sa.Index('ix_videos_category_tag', 'category_tag'),
    )

    op.create_table(
        'jobs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('progress', sa.Float(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Index('ix_jobs_video_id', 'video_id'),
        sa.Index('ix_jobs_status', 'status'),
    )

    op.create_table(
        'tokens',
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('schema_version', sa.String(length=20), nullable=False),
        sa.Column('tokens_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'prompts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target', sa.String(length=20), nullable=False),
        sa.Column('prompt_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_prompts_video_id_target', 'prompts', ['video_id', 'target'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_prompts_video_id_target', table_name='prompts')
    op.drop_table('prompts')
    op.drop_table('tokens')
    op.drop_table('jobs')
    op.drop_table('videos')
