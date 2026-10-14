"""add evidence_bundles and evidence_bundle_items

Centrelink evidence bundles (docs/centrelink-ease-apply-plan.md, part 2): one PDF of Quick
Apply screenshots per "Collate evidence" click, and which applications went into it. An
application with a screenshot and no evidence_bundle_items row is "unreported". A
separate table rather than a column on matches, so the migration never rebuilds the
matches table (SQLite can't add a foreign-key column in place).

Revision ID: d8f3b6a1c527
Revises: c4e8a2f6b913
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8f3b6a1c527'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2f6b913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'evidence_bundles',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('user_id', _BIG, sa.ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('job_count', sa.Integer(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'evidence_bundle_items',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('bundle_id', _BIG, sa.ForeignKey('evidence_bundles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('match_id', _BIG, sa.ForeignKey('matches.id', ondelete='CASCADE'), nullable=False),
        sa.UniqueConstraint('match_id', name='uq_evidence_bundle_items_match'),
    )
    op.create_index('idx_evidence_bundle_items_bundle', 'evidence_bundle_items', ['bundle_id'])


def downgrade() -> None:
    op.drop_index('idx_evidence_bundle_items_bundle', table_name='evidence_bundle_items')
    op.drop_table('evidence_bundle_items')
    op.drop_table('evidence_bundles')
