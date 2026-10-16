"""add job_listings.apply_type and matches.applied_via

Quick Apply evidence (docs/centrelink-ease-apply-plan.md):
- job_listings.apply_type: 'quick' (Seek's own Quick Apply flow) or 'external' (the
  employer's site), read from the detail page's Apply button; NULL = not known yet.
- matches.applied_via: how applied_at was recorded. 'auto' = the extension saw Seek's
  "application sent" page; 'manual' = the user pressed Mark Applied. NULL on rows applied
  before this column existed.

Both nullable with no backfill: nothing recorded before tells us either value.

Revision ID: c4e8a2f6b913
Revises: b7e2d9a4c601
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f6b913'
down_revision: Union[str, Sequence[str], None] = 'b7e2d9a4c601'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_listings', sa.Column('apply_type', sa.Text(), nullable=True))
    op.add_column('matches', sa.Column('applied_via', sa.Text(), nullable=True))


def downgrade() -> None:
    # batch mode: SQLite can't drop a column in place on older versions
    with op.batch_alter_table('matches') as batch:
        batch.drop_column('applied_via')
    with op.batch_alter_table('job_listings') as batch:
        batch.drop_column('apply_type')
