"""add prowlarr fields to audiobook

Revision ID: f3b2a1c0d4e5
Revises: d0fac85afd0f
Create Date: 2026-01-12 05:53:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3b2a1c0d4e5"
down_revision: Union[str, None] = "d0fac85afd0f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.add_column(sa.Column("prowlarr_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_prowlarr_query", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("last_prowlarr_query")
        batch_op.drop_column("prowlarr_count")
