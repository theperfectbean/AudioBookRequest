"""add freeleech field to audiobook

Revision ID: 9b8c7d6e5f4a
Revises: f3b2a1c0d4e5
Create Date: 2026-01-13 18:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b8c7d6e5f4a"
down_revision: Union[str, None] = "f3b2a1c0d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "freeleech", sa.Boolean(), nullable=False, server_default="false"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("freeleech")
