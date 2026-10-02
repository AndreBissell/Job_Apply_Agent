"""add letter_runs, letter_run_steps and the requirements-checklist cache

Phase 3 of the cover-letter plan. letter_runs persists a pipeline run's state;
letter_run_steps logs each tool call; job_listings gains the cache for
analyze_job's output (it depends only on the job, so it lives on the global row).

Revision ID: c7a3e1f5d284
Revises: b9e4d2a7c316
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7a3e1f5d284'
down_revision: Union[str, Sequence[str], None] = 'b9e4d2a7c316'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column('job_listings', sa.Column('requirements_checklist', sa.Text(), nullable=True))
    op.add_column(
        'job_listings',
        sa.Column('requirements_checklist_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'letter_runs',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('match_id', _BIG, sa.ForeignKey('matches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('engine', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), server_default='running', nullable=False),
        sa.Column('state', sa.Text(), nullable=True),
        sa.Column('final_draft_version', sa.Integer(), nullable=True),
        sa.Column('tool_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Numeric(12, 6), server_default='0', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_letter_runs_match', 'letter_runs', ['match_id'])

    op.create_table(
        'letter_run_steps',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('run_id', _BIG, sa.ForeignKey('letter_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('tool', sa.Text(), nullable=False),
        sa.Column('args', sa.Text(), nullable=True),
        sa.Column('result_summary', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('run_id', 'seq', name='uq_letter_run_steps_seq'),
    )


def downgrade() -> None:
    op.drop_table('letter_run_steps')
    op.drop_index('idx_letter_runs_match', table_name='letter_runs')
    op.drop_table('letter_runs')
    op.drop_column('job_listings', 'requirements_checklist_at')
    op.drop_column('job_listings', 'requirements_checklist')
