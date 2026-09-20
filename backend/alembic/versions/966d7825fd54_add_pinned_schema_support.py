"""add pinned schema support

Revision ID: 966d7825fd54
Revises: 744f3855d12b
Create Date: 2026-09-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '966d7825fd54'
down_revision: Union[str, None] = '744f3855d12b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A genuinely new Postgres enum type, not a value added to an
    # existing one - same values as PipelineVersionReviewStatus, but
    # its own distinct type name. Sharing one Postgres enum type across
    # multiple columns is a known Alembic autogenerate pitfall this
    # project already hit once (Stage A's own migration) - this
    # sidesteps it the same way pipeline_version_file_review_status
    # already does for the code review track.
    #
    # Created explicitly here, ONCE, via .create(). The column
    # definition below then references the SAME type by name but with
    # create_type=False - confirmed directly against real, converging
    # sources on this exact Alembic pattern: reusing one Enum object
    # (or omitting create_type=False on the second reference) would
    # have Alembic try to CREATE TYPE a second time and fail with "type
    # already exists" - the identical error already hit once on this
    # project before, per PipelineVersionFile.review_status's own
    # comment about why it needed its own distinct enum type name in
    # the first place.
    op.execute(
        "CREATE TYPE pipeline_version_file_schema_review_status AS ENUM "
        "('draft', 'testing', 'pending_review', 'approved', 'rejected')"
    )

    # uses_pinned_schema: NOT NULL with a server_default - every
    # existing version predates this feature and correctly defaults to
    # not using it, no backfill ambiguity.
    op.add_column('pipeline_versions', sa.Column('uses_pinned_schema', sa.Boolean(), nullable=False, server_default='false'))

    # All three nullable - only populated for a file whose version has
    # uses_pinned_schema set. schema_review_status has no server
    # default (unlike review_status's Python-side default of 'draft')
    # because null here correctly means "not using pinned schemas at
    # all", not "pinned schema proposal not started yet" - those are
    # genuinely different states, and a NOT NULL default would collapse
    # them into one.
    op.add_column('pipeline_version_files', sa.Column('schema_ddl', sa.Text(), nullable=True))
    op.add_column(
        'pipeline_version_files',
        sa.Column(
            'schema_review_status',
            postgresql.ENUM(
                'draft', 'testing', 'pending_review', 'approved', 'rejected',
                name='pipeline_version_file_schema_review_status',
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column('pipeline_version_files', sa.Column('schema_applied_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_version_files', 'schema_applied_at')
    op.drop_column('pipeline_version_files', 'schema_review_status')
    op.drop_column('pipeline_version_files', 'schema_ddl')
    op.drop_column('pipeline_versions', 'uses_pinned_schema')
    op.execute("DROP TYPE pipeline_version_file_schema_review_status")
