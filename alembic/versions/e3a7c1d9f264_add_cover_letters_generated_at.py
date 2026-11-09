"""add cover_letters.generated_at

When the letter's generated text was last written (docs/letter-rewrite-plan.md, step 1).
``created_at`` only records the first write, and ``updated_at`` also moves when the user
saves edits, so neither says whether a letter predates a profile change.

Backfill: the job's latest letter-writing call in ``llm_usage`` (the one-shot's
``cover_letter``, the pipeline's ``generate_letter`` / ``revise_letter``), else the row's
``created_at`` (letters from before usage logging). Portable SQL: a correlated subquery
works the same on SQLite and Postgres.

Revision ID: e3a7c1d9f264
Revises: d8f3b6a1c527
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3a7c1d9f264'
down_revision: Union[str, Sequence[str], None] = 'd8f3b6a1c527'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cover_letters', sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE cover_letters SET generated_at = COALESCE(
            (SELECT MAX(u.created_at) FROM llm_usage u JOIN matches m ON m.job_id = u.job_id
             WHERE m.id = cover_letters.match_id
               AND u.task IN ('cover_letter', 'generate_letter', 'revise_letter')),
            created_at)
        """
    )


def downgrade() -> None:
    # batch mode: SQLite can't drop a column in place on older versions
    with op.batch_alter_table('cover_letters') as batch:
        batch.drop_column('generated_at')
