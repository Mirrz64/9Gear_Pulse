"""
SQLAlchemy models for the 9gear Pulse commercial schema.

This replaces the single-connection prototype tables (users,
pipeline_audit_logs, pipeline_schedules, analytics_reporting) used by
the local-dev SQLite build with the multi-tenant schema described in
docs/04_Database_Schema.md.

Postgres-only, deliberately: this uses native UUID, JSONB, and ENUM
types with no first-class SQLite equivalent, matching
docs/02_Technical_Requirements.md's explicit "Database: PostgreSQL"
choice. Local development against this schema should use the
docker-compose Postgres service (9gear_pulse_db), not the SQLite
fallback the rest of this project still uses.

Two additions beyond what 04_Database_Schema.md specs, both because the
doc itself either implies or explicitly calls for them:
  - `User`: every other table has an owner_id/actor_id FK into `users`,
    but auth (Clerk/Auth0) is separate, later work. This is the minimal
    table needed to make those foreign keys valid now - an auth
    integration will populate it later (e.g. via webhook), not this
    migration.
  - `PipelineVersion`: 04_Database_Schema.md explicitly says to add
    this "before the review UI ships, cheap to add now, annoying to
    retrofit later" - so it's included from the start rather than
    bolted on when the review UI (the next workstream) needs it.

`connection_profiles.encrypted_credentials` is a LargeBinary column
ready to hold KMS-encrypted bytes - this migration only creates the
column. Actual encryption/decryption is the next workstream
(encrypted credential storage + sandbox egress lockdown); nothing
writes real credentials here yet.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProjectStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class ConnectionType(str, enum.Enum):
    postgres = "postgres"
    snowflake = "snowflake"
    bigquery = "bigquery"
    s3 = "s3"


class PipelineStatus(str, enum.Enum):
    draft = "draft"
    testing = "testing"
    approved = "approved"
    scheduled = "scheduled"


class RunStatus(str, enum.Enum):
    success = "success"
    failed = "failed"
    retrying = "retrying"


class PipelineVersionReviewStatus(str, enum.Enum):
    draft = "draft"
    testing = "testing"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class PipelineReviewAction(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"


class ArchitectureStatus(str, enum.Enum):
    """The new, earlier gate: an AI-proposed plan (which real tables it
    intends to read, what it'll write, its approach) reviewed by a human
    BEFORE any code exists - not a replacement for the existing
    code-level review_status, a checkpoint that happens before it.
    Null on a version until propose-architecture is actually called.
    """
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # External auth provider's user ID (e.g. Clerk's `user_xxx`). Kept
    # deliberately minimal - the auth workstream owns keeping this in
    # sync; this migration just gives it somewhere to write to.
    auth_provider_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="owner")
    connection_profiles: Mapped[list["ConnectionProfile"]] = relationship(back_populates="owner")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status"),
        default=ProjectStatus.active,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="projects")
    pipelines: Mapped[list["Pipeline"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ConnectionProfile(Base):
    __tablename__ = "connection_profiles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    # Not listed in 04_Database_Schema.md's table detail, but
    # 03_MVP_User_Journey_Flow.md's Connection Profile form explicitly
    # includes "name" as the first field, so it's added here.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[ConnectionType] = mapped_column(
        SAEnum(ConnectionType, name="connection_type"), nullable=False
    )
    encrypted_credentials: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    schema_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    last_introspected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship(back_populates="connection_profiles")


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    source_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connection_profiles.id"), nullable=False
    )
    destination_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connection_profiles.id"), nullable=False
    )
    # Latest version's code, kept in sync with the newest PipelineVersion
    # row for cheap reads; PipelineVersion is the source of truth for history.
    generated_code: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[PipelineStatus] = mapped_column(
        SAEnum(PipelineStatus, name="pipeline_status"),
        default=PipelineStatus.draft,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="pipelines")
    versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan", order_by="PipelineVersion.version"
    )
    runs: Mapped[list["PipelineRun"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan"
    )
    schedule: Mapped[Optional["Schedule"]] = relationship(
        back_populates="pipeline", uselist=False, cascade="all, delete-orphan"
    )


class PipelineVersion(Base):
    """Every generated/edited version of a pipeline's code, so the
    review UI can diff across versions without a retrofit later.

    Single-file (generated_code populated directly) and multi-file
    (files populated, generated_code left as a placeholder until every
    file is approved, then set to a concatenated read-only view of all
    of them) versions coexist - a version is "multi-file" simply if it
    has any PipelineVersionFile rows, not a separate stored flag that
    could drift out of sync with reality.
    """

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "version", name="uq_pipeline_versions_pipeline_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_code: Mapped[str] = mapped_column(Text, nullable=False)
    # Null when a version came from an AI generation/self-heal pass
    # rather than a human edit.
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # The architecture-review gate: AI's proposed plan (real tables it
    # intends to read, destination, approach, and an explicit
    # feasible/not-feasible call) stored as structured JSON, reviewed
    # before generate_and_test_pipeline is allowed to run at all. Both
    # null until propose-architecture is actually called on this version.
    architecture_proposal: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    architecture_status: Mapped[Optional[ArchitectureStatus]] = mapped_column(
        SAEnum(ArchitectureStatus, name="architecture_status"), nullable=True
    )
    review_status: Mapped[PipelineVersionReviewStatus] = mapped_column(
        SAEnum(PipelineVersionReviewStatus, name="pipeline_version_review_status"),
        default=PipelineVersionReviewStatus.draft,
        nullable=False,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline: Mapped["Pipeline"] = relationship(back_populates="versions")
    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="pipeline_version")
    reviews: Mapped[list["PipelineReview"]] = relationship(
        back_populates="pipeline_version", cascade="all, delete-orphan"
    )
    files: Mapped[list["PipelineVersionFile"]] = relationship(
        back_populates="pipeline_version", cascade="all, delete-orphan",
        order_by="PipelineVersionFile.file_order",
    )


class PipelineVersionFile(Base):
    """One file within a multi-file pipeline version (Stage B).

    Files are created all at once, in order, the moment an
    architecture proposal with a `files` plan is approved - purpose,
    reads_from, and destination_table come directly from that approved
    plan and don't change afterward; only generated_code and
    review_status move as each file is individually generated, sandbox-
    tested, and reviewed.

    Sequential and materialized by design: file N's reads_from can
    only be a real source table or an EARLIER file's destination_table
    in this same version - never a later one. That's what lets each
    file be sandbox-tested and reviewed on its own, against tables that
    already genuinely exist, rather than needing the whole pipeline
    built before any of it can be verified.
    """

    __tablename__ = "pipeline_version_files"
    __table_args__ = (
        UniqueConstraint("pipeline_version_id", "file_order", name="uq_pvf_version_order"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=False, index=True
    )
    file_order: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    # Real schema table names and/or earlier files' destination_table
    # values - a list since a later-stage file (e.g. a gold-layer
    # aggregation) may join more than one upstream table.
    reads_from: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    destination_table: Mapped[str] = mapped_column(String(255), nullable=False)
    # Null until this specific file has actually been generated -
    # distinct from the whole version's generated_code, which stays a
    # placeholder for a multi-file version until every file is approved.
    generated_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Reuses PipelineVersionReviewStatus (same Python enum, same values:
    # draft/testing/pending_review/approved/rejected) but a DISTINCT
    # Postgres enum type name - sharing one Postgres enum type across
    # two different tables' columns is a known Alembic autogenerate
    # pitfall (the exact "type already exists" error Stage A's own
    # migration hit), so this sidesteps that entirely rather than risk
    # repeating it.
    review_status: Mapped[PipelineVersionReviewStatus] = mapped_column(
        SAEnum(PipelineVersionReviewStatus, name="pipeline_version_file_review_status"),
        default=PipelineVersionReviewStatus.draft,
        nullable=False,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_version: Mapped["PipelineVersion"] = relationship(back_populates="files")
    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="pipeline_version_file")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id"), nullable=False, index=True
    )
    # Existing historical rows have no version. New sandbox runs must point
    # to the exact immutable code version they tested.
    pipeline_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=True, index=True
    )
    # Null for single-file versions and for whole-version runs. Set when
    # this run tested one specific file within a multi-file version -
    # still ALSO sets pipeline_version_id above, so existing queries
    # that just check "is there a successful run for this version"
    # (e.g. the whole-version approve endpoint) keep working unchanged.
    pipeline_version_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_version_files.id"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus, name="run_status"), nullable=False)
    log_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Populated by data_quality.run_quality_checks() after a successful
    # sandbox run - column-level null rates and any warnings (e.g. a
    # column that ended up entirely empty despite a "successful" load).
    # Informational only, never blocks approval - the human reviewer
    # decides what a warning means, same as the rest of the review gate.
    quality_checks: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    pipeline: Mapped["Pipeline"] = relationship(back_populates="runs")
    pipeline_version: Mapped[Optional["PipelineVersion"]] = relationship(back_populates="runs")
    pipeline_version_file: Mapped[Optional["PipelineVersionFile"]] = relationship(back_populates="runs")


class PipelineReview(Base):
    """Append-only record of a human approval or rejection."""

    __tablename__ = "pipeline_reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=False, index=True
    )
    # Null for a whole-version or architecture review. Set when this
    # review is about one specific file within a multi-file version -
    # same reasoning as PipelineRun.pipeline_version_file_id: lets
    # per-file rejection feedback exist without a separate table.
    pipeline_version_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_version_files.id"), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[PipelineReviewAction] = mapped_column(
        SAEnum(PipelineReviewAction, name="pipeline_review_action"), nullable=False
    )
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_version: Mapped["PipelineVersion"] = relationship(back_populates="reviews")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # One active schedule per pipeline for v1 - matches the MVP flow
    # ("the user sets a schedule"), singular, not a list.
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id"), nullable=False, unique=True, index=True
    )
    # A schedule is pinned to the version that passed the approval gate.
    pipeline_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=True
    )
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline: Mapped["Pipeline"] = relationship(back_populates="schedule")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Null actor_id is allowed for system-initiated actions (e.g. an
    # AI self-heal pass), not just human ones.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
