"""add llm_usage

One row per LLM call (tokens + estimated USD) for the budget guard and per-run
cost reporting. job_id / match_id / run_id are plain nullable labels, not FKs,
so the spend log survives deletion of the rows it describes.

Revision ID: b9e4d2a7c316
Revises: a7d2c9e15b48
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b9e4d2a7c316'
down_revision: Union[str, Sequence[str], None] = 'a7d2c9e15b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'llm_usage',
        sa.Column('id', _BIG, primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('task', sa.Text(), nullable=False),
        sa.Column('tier', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('thinking_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cached_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Numeric(12, 6), server_default='0', nullable=False),
        sa.Column('duration_ms', sa.Integer(), server_default='0', nullable=False),
        sa.Column('job_id', _BIG, nullable=True),
        sa.Column('match_id', _BIG, nullable=True),
        sa.Column('run_id', _BIG, nullable=True),
    )
    op.create_index('idx_llm_usage_created', 'llm_usage', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_llm_usage_created', table_name='llm_usage')
    op.drop_table('llm_usage')
