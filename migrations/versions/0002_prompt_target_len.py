"""increase prompt target column width for custom model names."""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002_prompt_target_len'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'prompts',
        'target',
        existing_type=sa.String(length=20),
        type_=sa.String(length=80),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'prompts',
        'target',
        existing_type=sa.String(length=80),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
