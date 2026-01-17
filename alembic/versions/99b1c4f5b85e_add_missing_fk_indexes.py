"""add_missing_fk_indexes

Revision ID: 99b1c4f5b85e
Revises: 9c307d2a3b4f
Create Date: 2026-01-17 17:58:22.796227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '99b1c4f5b85e'
down_revision: Union[str, None] = '9c307d2a3b4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add index on user_username for queries filtering by user alone
    # The composite primary key (asin, user_username) already indexes asin lookups
    # but not efficient for user_username-only queries
    op.create_index(
        'ix_audiobookrequest_user_username',
        'audiobookrequest',
        ['user_username']
    )

    # Add index on search_key for MetadataCache queries
    # While search_key is part of composite PK (search_key, provider),
    # an explicit index ensures efficient lookups on search_key alone
    op.create_index(
        'ix_metadatacache_search_key',
        'metadatacache',
        ['search_key']
    )


def downgrade() -> None:
    op.drop_index('ix_metadatacache_search_key', table_name='metadatacache')
    op.drop_index('ix_audiobookrequest_user_username', table_name='audiobookrequest')
