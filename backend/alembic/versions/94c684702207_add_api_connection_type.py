"""add api connection type

Revision ID: 94c684702207
Revises: 24b78cfa699e
Create Date: 2026-09-01 01:59:18.131693

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '94c684702207'
down_revision: Union[str, None] = '24b78cfa699e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a normal transaction
    # block, and even on Postgres versions where it technically can,
    # the new value can't be referenced until that statement is
    # committed. autocommit_block() steps outside Alembic's normal
    # transactional DDL wrapper specifically for this one statement.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'api'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all. Removing an
    # enum value safely requires renaming the type, recreating it
    # without the value, and migrating every column over - genuinely
    # risky to automate if any real connection_profiles row is already
    # using 'api' by the time this would run. Left deliberately
    # unimplemented rather than risk silent data loss for one value.
    raise NotImplementedError(
        "Downgrading this migration would require manually removing 'api' "
        "from the connection_type enum, which Postgres doesn't support "
        "directly and isn't worth automating for a single added value."
    )