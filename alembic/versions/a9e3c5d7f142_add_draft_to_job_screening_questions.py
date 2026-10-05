"""add job_screening_questions.draft

Phase 9c of the cover-letter plan: the app's draft answer to an open-ended Quick
Apply question (JSON TEXT, with a fingerprint of the profile it was built from so a
profile change marks it stale). Made only on the user's click.

Revision ID: a9e3c5d7f142
Revises: b4d8e2f6a913
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a9e3c5d7f142'
down_revision: Union[str, Sequence[str], None] = 'b4d8e2f6a913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_screening_questions', sa.Column('draft', sa.Text(), nullable=True))


def downgrade() -> None:
    # batch mode: SQLite can't drop a column in place on older versions
    with op.batch_alter_table('job_screening_questions') as batch:
        batch.drop_column('draft')
