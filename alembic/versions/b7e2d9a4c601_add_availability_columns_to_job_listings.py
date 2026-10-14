"""add availability columns to job_listings

Background availability re-checks (app/availability.py, docs/database-schema.md):
- last_seen_live_at: when the listing was last seen live. Backfilled from date_scraped for
  every row that has a description, so only jobs captured over a day ago need a re-check
  before the LLM pipeline spends on them.
- availability_checked_at: when the last re-check verdict (any kind) was recorded.
- availability_misses: consecutive "description gone" verdicts; two flag the job expired.

Revision ID: b7e2d9a4c601
Revises: d5f1b8c3e9a2
Create Date: 2026-10-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7e2d9a4c601'
down_revision: Union[str, Sequence[str], None] = 'd5f1b8c3e9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_listings', sa.Column('last_seen_live_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('job_listings', sa.Column('availability_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'job_listings',
        sa.Column('availability_misses', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    # Plain portable SQL (no dialect functions): a captured listing was live when captured.
    op.execute(
        "UPDATE job_listings SET last_seen_live_at = date_scraped "
        "WHERE raw_description IS NOT NULL AND expired_detected_at IS NULL"
    )


def downgrade() -> None:
    # batch mode: SQLite can't drop a column in place on older versions
    with op.batch_alter_table('job_listings') as batch:
        batch.drop_column('availability_misses')
        batch.drop_column('availability_checked_at')
        batch.drop_column('last_seen_live_at')
