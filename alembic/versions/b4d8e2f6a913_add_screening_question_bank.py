"""add the Quick Apply question bank: screening_questions, job_screening_questions

Phase 9a of the cover-letter plan (§10.1). screening_questions is the global bank of
employer questions read from Seek's Quick Apply "Answer employer questions" step
(identity = Seek library id or a fingerprint; kind / strategy / parameters from the
sorting layers; status new -> confirmed after the user's review).
job_screening_questions links a job to the bank questions its form had, in order,
with Seek's per-form question id, field name and option values. No answers stored.

Revision ID: b4d8e2f6a913
Revises: f6a9c3d8e217
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4d8e2f6a913'
down_revision: Union[str, Sequence[str], None] = 'f6a9c3d8e217'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'screening_questions',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('identity_key', sa.Text(), nullable=False),
        sa.Column('library_id', sa.Text(), nullable=True),
        sa.Column('library_version', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('normalised_text', sa.Text(), nullable=False),
        sa.Column('input_type', sa.Text(), nullable=False),
        sa.Column('options', sa.Text(), nullable=True),
        sa.Column('kind', sa.Text(), server_default='unknown', nullable=False),
        sa.Column('strategy', sa.Text(), nullable=True),
        sa.Column('parameters', sa.Text(), nullable=True),
        sa.Column('classified_by', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default='new', nullable=False),
        sa.Column('times_seen', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('identity_key', name='uq_screening_questions_identity_key'),
    )
    op.create_index('idx_screening_questions_status', 'screening_questions', ['status'])

    op.create_table(
        'job_screening_questions',
        sa.Column('job_id', _BIG, sa.ForeignKey('job_listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('question_id', _BIG, sa.ForeignKey('screening_questions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('seek_question_id', sa.Text(), nullable=False),
        sa.Column('field_name', sa.Text(), nullable=False),
        sa.Column('option_values', sa.Text(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('job_id', 'question_id'),
    )
    op.create_index('idx_job_screening_questions_question', 'job_screening_questions', ['question_id'])


def downgrade() -> None:
    op.drop_index('idx_job_screening_questions_question', table_name='job_screening_questions')
    op.drop_table('job_screening_questions')
    op.drop_index('idx_screening_questions_status', table_name='screening_questions')
    op.drop_table('screening_questions')
