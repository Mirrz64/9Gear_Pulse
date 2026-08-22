"""add pipeline review gate

Revision ID: d1f4a9b8c2e0
Revises: 8263914a0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f4a9b8c2e0"
down_revision: Union[str, None] = "8263914a0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    review_status = postgresql.ENUM("draft", "testing", "pending_review", "approved", "rejected", name="pipeline_version_review_status")
    review_action = postgresql.ENUM("approved", "rejected", name="pipeline_review_action")
    review_status.create(op.get_bind(), checkfirst=True)
    review_action.create(op.get_bind(), checkfirst=True)
    op.add_column("pipeline_versions", sa.Column("review_status", review_status, nullable=False, server_default="draft"))
    op.add_column("pipeline_versions", sa.Column("reviewed_by", sa.UUID(), nullable=True))
    op.add_column("pipeline_versions", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_pipeline_versions_reviewed_by_users", "pipeline_versions", "users", ["reviewed_by"], ["id"])
    op.create_unique_constraint("uq_pipeline_versions_pipeline_version", "pipeline_versions", ["pipeline_id", "version"])
    op.add_column("pipeline_runs", sa.Column("pipeline_version_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_pipeline_runs_pipeline_version", "pipeline_runs", "pipeline_versions", ["pipeline_version_id"], ["id"])
    op.create_index("ix_pipeline_runs_pipeline_version_id", "pipeline_runs", ["pipeline_version_id"])
    op.create_table(
        "pipeline_reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pipeline_version_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        # The enum was created explicitly above; prevent create_table from
        # emitting a second CREATE TYPE for it.
        sa.Column("action", postgresql.ENUM(name="pipeline_review_action", create_type=False), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["pipeline_version_id"], ["pipeline_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_reviews_pipeline_version_id", "pipeline_reviews", ["pipeline_version_id"])
    op.add_column("schedules", sa.Column("pipeline_version_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_schedules_pipeline_version", "schedules", "pipeline_versions", ["pipeline_version_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_schedules_pipeline_version", "schedules", type_="foreignkey")
    op.drop_column("schedules", "pipeline_version_id")
    op.drop_index("ix_pipeline_reviews_pipeline_version_id", table_name="pipeline_reviews")
    op.drop_table("pipeline_reviews")
    op.drop_index("ix_pipeline_runs_pipeline_version_id", table_name="pipeline_runs")
    op.drop_constraint("fk_pipeline_runs_pipeline_version", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "pipeline_version_id")
    op.drop_constraint("uq_pipeline_versions_pipeline_version", "pipeline_versions", type_="unique")
    op.drop_constraint("fk_pipeline_versions_reviewed_by_users", "pipeline_versions", type_="foreignkey")
    op.drop_column("pipeline_versions", "reviewed_at")
    op.drop_column("pipeline_versions", "reviewed_by")
    op.drop_column("pipeline_versions", "review_status")
    sa.Enum(name="pipeline_review_action").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="pipeline_version_review_status").drop(op.get_bind(), checkfirst=True)
