"""add healing events ledger

Revision ID: c3d81f0a9b52
Revises: 4cf90f1d852e
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d81f0a9b52'
down_revision: Union[str, None] = '4cf90f1d852e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A brand-new table with no Postgres enum columns on purpose - trigger
    # and outcome are plain strings (see HealingEvent's docstring), so
    # this needs none of the CREATE TYPE handling the enum-adding
    # migrations in this project do.
    op.create_table(
        'healing_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('pipeline_id', sa.UUID(), nullable=False),
        sa.Column('pipeline_version_id', sa.UUID(), nullable=True),
        sa.Column('pipeline_version_file_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('trigger', sa.String(length=32), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('source_type', sa.String(length=32), nullable=True),
        sa.Column('destination_type', sa.String(length=32), nullable=True),
        sa.Column('exception_type', sa.String(length=200), nullable=True),
        sa.Column('error_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_log', sa.Text(), nullable=True),
        sa.Column('code_before', sa.Text(), nullable=True),
        sa.Column('code_after', sa.Text(), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('changes_made', sa.Text(), nullable=True),
        sa.Column('same_error', sa.Boolean(), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pipeline_version_id'], ['pipeline_versions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['pipeline_version_file_id'], ['pipeline_version_files.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_healing_events_project_id'), 'healing_events', ['project_id'], unique=False)
    op.create_index(op.f('ix_healing_events_pipeline_id'), 'healing_events', ['pipeline_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_healing_events_pipeline_id'), table_name='healing_events')
    op.drop_index(op.f('ix_healing_events_project_id'), table_name='healing_events')
    op.drop_table('healing_events')
