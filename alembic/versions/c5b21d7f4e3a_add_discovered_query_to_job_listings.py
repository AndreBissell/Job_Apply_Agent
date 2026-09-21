"""add discovered_query to job_listings

Records which Seek search surfaced each listing, so search performance becomes
measurable (yield = share of a query's jobs that scored well). Nullable: every
row captured before this migration, and every job opened directly rather than
from a results page, legitimately has no originating query.

Revision ID: c5b21d7f4e3a
Revises: e1c4a7b90d25
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5b21d7f4e3a'
down_revision: Union[str, Sequence[str], None] = 'e1c4a7b90d25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_listings', sa.Column('discovered_query', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_listings', 'discovered_query')
