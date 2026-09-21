"""add screenshot fields to matches

Revision ID: cd7884c6a5c7
Revises: a8e76ddffff8
Create Date: 2026-09-18 15:27:53.358647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd7884c6a5c7'
down_revision: Union[str, Sequence[str], None] = 'a8e76ddffff8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('screenshot_path', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('screenshot_taken_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('screenshot_taken_at')
        batch_op.drop_column('screenshot_path')
