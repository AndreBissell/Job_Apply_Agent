"""add quick screen fields to job listings

Revision ID: a8e76ddffff8
Revises: 60d65ffb16df
Create Date: 2026-09-18 15:14:28.472857

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8e76ddffff8'
down_revision: Union[str, Sequence[str], None] = '60d65ffb16df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quick_screen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('quick_screen_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.drop_column('quick_screen_score')
        batch_op.drop_column('quick_screen_at')
