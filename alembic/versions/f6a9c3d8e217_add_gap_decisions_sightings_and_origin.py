"""add gap_decisions, gap_sightings and an origin tag on profile rows

Phase 7b of the cover-letter plan. gap_decisions remembers the user's "No"
answers to ask_user questions (each gap is asked once; it is also the
to-work-on list); gap_sightings counts the ads that asked for each one
(job_id is a label, not an FK, so job purges don't erase counts). The nullable
origin column on qualifications / experiences / skills marks rows created from
an ask_user "Yes" (plan Q12). letter_runs gets a status index for the
waiting_user / answered lookups.

Revision ID: f6a9c3d8e217
Revises: e8b4f2a61c93
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a9c3d8e217'
down_revision: Union[str, Sequence[str], None] = 'e8b4f2a61c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_ORIGIN_TABLES = ('qualifications', 'experiences', 'skills')


def upgrade() -> None:
    for table in _ORIGIN_TABLES:
        op.add_column(table, sa.Column('origin', sa.Text(), nullable=True))

    op.create_table(
        'gap_decisions',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('user_id', _BIG, sa.ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('skill_key', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('requirement_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'skill_key', name='uq_gap_decisions_user_key'),
    )

    op.create_table(
        'gap_sightings',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('gap_id', _BIG, sa.ForeignKey('gap_decisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', _BIG, nullable=False),
        sa.Column('job_title', sa.Text(), nullable=True),
        sa.Column('importance', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('gap_id', 'job_id', name='uq_gap_sightings_gap_job'),
    )
    op.create_index('idx_gap_sightings_seen', 'gap_sightings', ['seen_at'])
    op.create_index('idx_letter_runs_status', 'letter_runs', ['status'])


def downgrade() -> None:
    op.drop_index('idx_letter_runs_status', table_name='letter_runs')
    op.drop_index('idx_gap_sightings_seen', table_name='gap_sightings')
    op.drop_table('gap_sightings')
    op.drop_table('gap_decisions')
    for table in _ORIGIN_TABLES:
        op.drop_column(table, 'origin')
