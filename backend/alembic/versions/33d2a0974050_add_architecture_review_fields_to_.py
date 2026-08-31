"""add architecture review fields to pipeline_versions

Revision ID: 33d2a0974050
Revises: 5b624203dc68
Create Date: 2026-08-31 11:21:03.574660

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '33d2a0974050'
down_revision: Union[str, None] = '5b624203dc68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres ENUMs are their own database object - unlike creating a
    # brand-new table with an enum column (which SQLAlchemy handles
    # automatically), adding an enum column to an EXISTING table via
    # op.add_column() does not create the type first on its own. Create
    # it explicitly, then reference it with create_type=False so
    # add_column doesn't try to create the same type a second time.
    architecture_status_enum = postgresql.ENUM(
        'pending_review', 'approved', 'rejected', name='architecture_status'
    )
    architecture_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'pipeline_versions',
        sa.Column('architecture_proposal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'pipeline_versions',
        sa.Column(
            'architecture_status',
            postgresql.ENUM(
                'pending_review', 'approved', 'rejected',
                name='architecture_status', create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('pipeline_versions', 'architecture_status')
    op.drop_column('pipeline_versions', 'architecture_proposal')
    postgresql.ENUM(name='architecture_status').drop(op.get_bind(), checkfirst=True)
