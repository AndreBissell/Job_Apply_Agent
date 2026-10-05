"""add matches.interview_at

The Centrelink dashboard's "+ Interview" button (docs/centrelink-dashboard-plan.md):
when the user recorded an interview for an application. Status is unchanged.

Revision ID: d5f1b8c3e9a2
Revises: a9e3c5d7f142
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5f1b8c3e9a2'
down_revision: Union[str, Sequence[str], None] = 'a9e3c5d7f142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('interview_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # batch mode: SQLite can't drop a column in place on older versions
    with op.batch_alter_table('matches') as batch:
        batch.drop_column('interview_at')
