"""add pipeline version scaffold files

Revision ID: b6902b6c94d6
Revises: ce985346710c
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b6902b6c94d6'
down_revision: Union[str, None] = 'ce985346710c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both enum columns below are being created as part of a brand-new
    # table, not added to an existing one - same as
    # pipeline_version_files' own review_status column in
    # 24b78cfa699e, this needs no separate explicit .create() call the
    # way an ALTER on an existing table would (see 33d2a0974050's
    # comment for why that case is different).
    op.create_table(
        'pipeline_version_scaffold_files',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('pipeline_version_id', sa.UUID(), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('scaffold_type', sa.Enum(
            'docker_compose', 'env_example', 'gitignore', 'requirements', 'airflow_dag', 'other',
            name='scaffold_file_type',
        ), nullable=False),
        sa.Column('generated_content', sa.Text(), nullable=True),
        # Distinct Postgres enum type name from both pipeline_versions'
        # and pipeline_version_files' own review_status columns - same
        # reasoning as models.py's own comment: sharing one enum type
        # across tables is a known Alembic autogenerate pitfall.
        sa.Column('review_status', sa.Enum(
            'draft', 'testing', 'pending_review', 'approved', 'rejected',
            name='pipeline_version_scaffold_file_review_status',
        ), nullable=False),
        sa.Column('reviewed_by', sa.UUID(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['pipeline_version_id'], ['pipeline_versions.id'], ),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_pipeline_version_scaffold_files_pipeline_version_id'),
        'pipeline_version_scaffold_files', ['pipeline_version_id'], unique=False,
    )

    # Same linking pattern 24b78cfa699e used to connect pipeline_reviews
    # to pipeline_version_files - a nullable FK so a review row can
    # optionally point at the one scaffold file it was about.
    op.add_column('pipeline_reviews', sa.Column('pipeline_version_scaffold_file_id', sa.UUID(), nullable=True))
    op.create_index(
        op.f('ix_pipeline_reviews_pipeline_version_scaffold_file_id'),
        'pipeline_reviews', ['pipeline_version_scaffold_file_id'], unique=False,
    )
    op.create_foreign_key(
        None, 'pipeline_reviews', 'pipeline_version_scaffold_files',
        ['pipeline_version_scaffold_file_id'], ['id'],
    )


def downgrade() -> None:
    # Unlike the connection_type ALTER TYPE ADD VALUE migrations, both
    # enums here are being newly introduced alongside their own new
    # table - no existing row anywhere could already depend on them,
    # so a full, clean downgrade is safe to automate (same reasoning
    # as 33d2a0974050's and d1f4a9b8c2e0's own downgrades).
    op.drop_constraint(None, 'pipeline_reviews', type_='foreignkey')
    op.drop_index(
        op.f('ix_pipeline_reviews_pipeline_version_scaffold_file_id'), table_name='pipeline_reviews',
    )
    op.drop_column('pipeline_reviews', 'pipeline_version_scaffold_file_id')

    op.drop_index(
        op.f('ix_pipeline_version_scaffold_files_pipeline_version_id'),
        table_name='pipeline_version_scaffold_files',
    )
    op.drop_table('pipeline_version_scaffold_files')

    sa.Enum(name='pipeline_version_scaffold_file_review_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='scaffold_file_type').drop(op.get_bind(), checkfirst=True)
