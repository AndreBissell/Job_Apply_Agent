"""add matches.scored_at, profiles.profile_revised_at, and a matches(created_at) index

Supports the rolling retention window and the post-profile-update weighting in
the search-suggestion miner (see app/retention.py):

* ``matches.scored_at`` — when the score was last written. A job re-scored
  against an updated profile keeps its old ``created_at``, so created_at cannot
  say whether a score reflects the *current* profile. Left NULL on existing
  rows on purpose: they are read as created_at, which is the honest answer
  ("scored around when it was captured"), and inventing a backfill would
  claim precision we don't have.
* ``profiles.profile_revised_at`` — explicit "profile content changed" marker,
  for the one case MAX(updated_at) over the child tables cannot see: a
  deletion.
* ``idx_matches_created`` — the sweep and the miner both range-scan
  matches by created_at.

Revision ID: a7d2c9e15b48
Revises: d3f8a1c60b92
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7d2c9e15b48'
down_revision: Union[str, Sequence[str], None] = 'd3f8a1c60b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'matches', sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'profiles',
        sa.Column('profile_revised_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_matches_created', 'matches', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_matches_created', table_name='matches')
    op.drop_column('profiles', 'profile_revised_at')
    op.drop_column('matches', 'scored_at')
