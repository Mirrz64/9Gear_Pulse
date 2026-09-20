"""add graphql to connection_type enum

Revision ID: 744f3855d12b
Revises: 007fe8ddee3b
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '744f3855d12b'
down_revision: Union[str, None] = '007fe8ddee3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reasoning as 007fe8ddee3b's own upgrade() (adding 'redis') -
    # safe inside a normal transaction on Postgres 12+, since nothing
    # in this migration writes a row using 'graphql' as connection_type.
    op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'graphql'")


def downgrade() -> None:
    # Same reasoning as 007fe8ddee3b's own downgrade() - Postgres has no
    # ALTER TYPE ... DROP VALUE at all, and this migration never wrote a
    # row using the value, so there is genuinely nothing to undo here.
    pass
