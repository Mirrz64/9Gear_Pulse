"""add directory to pipeline_version_files

Revision ID: 0347b4c93da5
Revises: b6902b6c94d6
Create Date: 2026-09-11 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0347b4c93da5'
down_revision: Union[str, None] = 'b6902b6c94d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill needed - every existing row predates this
    # column and simply has no folder placement, which is exactly what
    # null already means for it (project root).
    op.add_column('pipeline_version_files', sa.Column('directory', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_version_files', 'directory')
