"""add generation_in_progress to pipeline_versions and pipeline_version_files

Revision ID: 576bbff90b53
Revises: 0347b4c93da5
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '576bbff90b53'
down_revision: Union[str, None] = '0347b4c93da5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with a server_default - unlike the earlier nullable
    # directory column, "false" is unambiguously correct for every
    # existing row: nothing already in the table is actually being
    # worked on by a background task right now, so no backfill
    # ambiguity, and no reason to allow null at all going forward.
    op.add_column('pipeline_versions', sa.Column('generation_in_progress', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('pipeline_version_files', sa.Column('generation_in_progress', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('pipeline_version_files', 'generation_in_progress')
    op.drop_column('pipeline_versions', 'generation_in_progress')
