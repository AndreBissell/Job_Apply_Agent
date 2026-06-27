"""add profile fields: phone location summary target_role target_location

Revision ID: b3f2e9c1d4a7
Revises: f42bcd31e385
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f2e9c1d4a7'
down_revision: Union[str, Sequence[str], None] = 'f42bcd31e385'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add profile fields used by the profile UI and LLM pipeline."""
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('location', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('target_role', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('target_location', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove profile fields."""
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.drop_column('target_location')
        batch_op.drop_column('target_role')
        batch_op.drop_column('summary')
        batch_op.drop_column('location')
        batch_op.drop_column('phone')
