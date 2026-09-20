"""add soap to connection_type enum

Revision ID: 75585cd046f2
Revises: ac46a997e99e
Create Date: 2026-09-18 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '75585cd046f2'
down_revision: Union[str, None] = 'ac46a997e99e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reasoning as every prior connection_type value addition
    # (redis, graphql) - safe inside a normal transaction on Postgres
    # 12+, since nothing in this migration writes a row using 'soap' as
    # connection_type.
    op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'soap'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all, and this
    # migration never wrote a row using the value, so there is
    # genuinely nothing to undo here - same as every prior enum-value
    # addition migration in this project.
    pass
