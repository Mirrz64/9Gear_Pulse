"""add optional project context fields (objectives, dataset_notes, known_constraints, load_pattern)

Revision ID: 4cf90f1d852e
Revises: 7f39e448d2ac
Create Date: 2026-09-22 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4cf90f1d852e'
down_revision: Union[str, None] = '7f39e448d2ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same two-step enum pattern used throughout this project (CREATE
    # TYPE via raw SQL, then create_type=False in the column def) -
    # the confirmed fix for Alembic's own double-CREATE-TYPE pitfall
    # (sa.Enum(...).create() plus the same Enum object in add_column
    # tries to create the type twice).
    op.execute("CREATE TYPE load_pattern AS ENUM ('full_refresh', 'append_only', 'incremental')")

    op.add_column('projects', sa.Column('objectives', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('dataset_notes', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('known_constraints', sa.Text(), nullable=True))
    op.add_column(
        'projects',
        sa.Column('load_pattern', postgresql.ENUM('full_refresh', 'append_only', 'incremental', name='load_pattern', create_type=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('projects', 'load_pattern')
    op.drop_column('projects', 'known_constraints')
    op.drop_column('projects', 'dataset_notes')
    op.drop_column('projects', 'objectives')
    op.execute("DROP TYPE load_pattern")
