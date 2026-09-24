"""add azure_blob to connection_type enum

Revision ID: 7f39e448d2ac
Revises: f06a012af6a5
Create Date: 2026-09-18 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7f39e448d2ac'
down_revision: Union[str, None] = 'f06a012af6a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reasoning as every prior connection_type value addition -
    # safe inside a normal transaction on Postgres 12+, since nothing
    # in this migration writes a row using 'azure_blob' as
    # connection_type.
    op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'azure_blob'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all, and this
    # migration never wrote a row using the value, so there is
    # genuinely nothing to undo here - same as every prior enum-value
    # addition migration in this project.
    pass
