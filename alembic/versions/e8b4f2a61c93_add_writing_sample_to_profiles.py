"""add profiles.writing_sample

Phase 4 of the cover-letter plan: the pasted "Your writing" dump the letter
writer uses as a voice reference (tone and rhythm only, never facts).

Revision ID: e8b4f2a61c93
Revises: c7a3e1f5d284
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8b4f2a61c93'
down_revision: Union[str, Sequence[str], None] = 'c7a3e1f5d284'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('profiles', sa.Column('writing_sample', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('profiles', 'writing_sample')
