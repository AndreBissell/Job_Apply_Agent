"""add hidden_at to matches

Turns the bulk "delete low-scoring jobs" action into a soft delete. The row
has to survive because the suggestion miner ranks each phrase against the
*baseline* of all scored matches: deleting the low scorers raises that baseline
and flattens the very contrast the ranking depends on (observed live —
dropping everything under 70 moved the dev profile's baseline from 69.4 to
82.5). The yield stats in GET /jobs/search-performance need them for the same
reason.

NULL = visible, which is what every pre-existing row should be.

Revision ID: d3f8a1c60b92
Revises: c5b21d7f4e3a
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c60b92'
down_revision: Union[str, Sequence[str], None] = 'c5b21d7f4e3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'matches',
        sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('matches', 'hidden_at')
