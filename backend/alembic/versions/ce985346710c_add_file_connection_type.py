"""add file connection type

Revision ID: ce985346710c
Revises: 94c684702207
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ce985346710c'
down_revision: Union[str, None] = '94c684702207'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same autocommit_block() workaround as 94c684702207 (add api
    # connection type) - ALTER TYPE ... ADD VALUE cannot run inside a
    # normal transaction block, and the new value can't be referenced
    # until that statement is committed.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE connection_type ADD VALUE IF NOT EXISTS 'file'")

    # One row per uploaded file within a file-upload connection profile
    # - see ConnectionProfileFile's docstring in models.py for why
    # format is a plain string rather than its own Postgres enum
    # (sidesteps the exact autogenerate-misses-enum-additions problem
    # this migration's ALTER TYPE above is already working around).
    op.create_table('connection_profile_files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('connection_profile_id', sa.UUID(), nullable=False),
    sa.Column('file_order', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('storage_path', sa.String(length=1024), nullable=False),
    sa.Column('format', sa.String(length=20), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['connection_profile_id'], ['connection_profiles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('connection_profile_id', 'file_order', name='uq_cpf_profile_order')
    )
    op.create_index(op.f('ix_connection_profile_files_connection_profile_id'), 'connection_profile_files', ['connection_profile_id'], unique=False)


def downgrade() -> None:
    # Unlike 94c684702207, this migration has a genuinely safe partial
    # downgrade available: connection_profile_files is a brand new
    # table with no shared enum column, so dropping it carries none of
    # the "might already be in use elsewhere" risk that blocked the
    # previous migration's downgrade entirely.
    op.drop_index(op.f('ix_connection_profile_files_connection_profile_id'), table_name='connection_profile_files')
    op.drop_table('connection_profile_files')

    # The 'file' value added to connection_type above is NOT removed -
    # same Postgres limitation as before (no ALTER TYPE ... DROP VALUE).
    # It stays a valid-but-unused value on the enum after this downgrade,
    # which is harmless: no column can be pointed at a dropped table, so
    # nothing can actually create a 'file'-typed profile that would need
    # this table until upgrade() runs again.
