"""add azure_sql to connection_type enum

Revision ID: f06a012af6a5
Revises: 75585cd046f2
Create Date: 2026-09-18 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f06a012af6a5'
down_revision: Union[str, None] = '75585cd046f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reasoning as every prior connection_type value addition
    # (redis, graphql, soap) - safe inside a normal transaction on
    # Postgres 12+, since nothing in this migration writes a row using
    # 'azure_sql' as connection_type.
    op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'azure_sql'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all, and this
    # migration never wrote a row using the value, so there is
    # genuinely nothing to undo here - same as every prior enum-value
    # addition migration in this project.
    pass
