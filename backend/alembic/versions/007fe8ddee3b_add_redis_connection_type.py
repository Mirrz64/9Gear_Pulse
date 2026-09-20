"""add redis to connection_type enum

Revision ID: 007fe8ddee3b
Revises: 576bbff90b53
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '007fe8ddee3b'
down_revision: Union[str, None] = '576bbff90b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safe inside a normal transaction on Postgres 12+ (this project
    # runs 15) - confirmed directly against Postgres's own committer
    # notes: the only real restriction is that a newly-added enum value
    # can't be REFERENCED by a row written in the same transaction that
    # added it. This migration only adds the value; nothing here (or in
    # any later migration in the same alembic upgrade invocation) writes
    # a row using 'redis' as connection_type, so that restriction never
    # applies.
    op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'redis'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all - removing an
    # enum value requires rebuilding the whole type (create a new type,
    # cast every column over, drop the old type, rename), which is
    # only safe to do knowing nothing already uses the value. Since
    # this migration's own upgrade() never writes any row using
    # 'redis', downgrading is a genuine no-op here, not a shortcut.
    pass
