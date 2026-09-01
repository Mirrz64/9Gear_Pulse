"""add multi-file pipeline generation (stage B)

Revision ID: 24b78cfa699e
Revises: 33d2a0974050
Create Date: 2026-08-31 18:22:59.414890

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '24b78cfa699e'
down_revision: Union[str, None] = '33d2a0974050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pipeline_version_files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('pipeline_version_id', sa.UUID(), nullable=False),
    sa.Column('file_order', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('reads_from', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('destination_table', sa.String(length=255), nullable=False),
    sa.Column('generated_code', sa.Text(), nullable=True),
    sa.Column('review_status', sa.Enum('draft', 'testing', 'pending_review', 'approved', 'rejected', name='pipeline_version_file_review_status'), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pipeline_version_id'], ['pipeline_versions.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pipeline_version_id', 'file_order', name='uq_pvf_version_order')
    )
    op.create_index(op.f('ix_pipeline_version_files_pipeline_version_id'), 'pipeline_version_files', ['pipeline_version_id'], unique=False)
    # NOTE: autogenerate also proposed dropping users_masked and three
    # _dlt_* tables here. Those are data-plane tables created by
    # generated pipelines actually running (dlt's own bookkeeping, plus
    # a real destination table from Stage A testing) - never declared
    # in models.py because they were never meant to be tracked by it.
    # Autogenerate can't tell the difference between "not modeled
    # because it's out of scope" and "should be deleted" - removed by
    # hand rather than applied.
    op.add_column('pipeline_reviews', sa.Column('pipeline_version_file_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_pipeline_reviews_pipeline_version_file_id'), 'pipeline_reviews', ['pipeline_version_file_id'], unique=False)
    op.create_foreign_key(None, 'pipeline_reviews', 'pipeline_version_files', ['pipeline_version_file_id'], ['id'])
    op.add_column('pipeline_runs', sa.Column('pipeline_version_file_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_pipeline_runs_pipeline_version_file_id'), 'pipeline_runs', ['pipeline_version_file_id'], unique=False)
    op.create_foreign_key(None, 'pipeline_runs', 'pipeline_version_files', ['pipeline_version_file_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(None, 'pipeline_runs', type_='foreignkey')
    op.drop_index(op.f('ix_pipeline_runs_pipeline_version_file_id'), table_name='pipeline_runs')
    op.drop_column('pipeline_runs', 'pipeline_version_file_id')
    op.drop_constraint(None, 'pipeline_reviews', type_='foreignkey')
    op.drop_index(op.f('ix_pipeline_reviews_pipeline_version_file_id'), table_name='pipeline_reviews')
    op.drop_column('pipeline_reviews', 'pipeline_version_file_id')
    op.drop_index(op.f('ix_pipeline_version_files_pipeline_version_id'), table_name='pipeline_version_files')
    op.drop_table('pipeline_version_files')
