"""add expired_detected_at to job_listings

Revision ID: 9beae9288236
Revises: cd7884c6a5c7
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9beae9288236'
down_revision: Union[str, Sequence[str], None] = 'cd7884c6a5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expired_detected_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.drop_column('expired_detected_at')
