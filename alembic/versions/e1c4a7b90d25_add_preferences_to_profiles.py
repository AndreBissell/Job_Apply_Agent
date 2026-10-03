"""add preferences to profiles

Revision ID: e1c4a7b90d25
Revises: 9beae9288236
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c4a7b90d25'
down_revision: Union[str, Sequence[str], None] = '9beae9288236'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferences', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.drop_column('preferences')
