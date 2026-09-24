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
    Boolean,
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


class LoadPattern(str, enum.Enum):
    # A user's stated preference for how the destination table should
    # be maintained, surfaced to propose_architecture.py so the AI's
    # approach and build plan reflect it rather than having to guess -
    # maps closely to dlt's own write_disposition concept downstream.
    full_refresh = "full_refresh"
    append_only = "append_only"
    incremental = "incremental"


class ConnectionType(str, enum.Enum):
    postgres = "postgres"
    snowflake = "snowflake"
    bigquery = "bigquery"
    s3 = "s3"
    api = "api"
    file = "file"
    redis = "redis"
    graphql = "graphql"
    soap = "soap"
    azure_sql = "azure_sql"
    azure_blob = "azure_blob"


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


class ScaffoldFileType(str, enum.Enum):
    """Mirrors propose_architecture.py's ScaffoldFile.scaffold_type
    string values exactly - kept as a real enum here (rather than a
    plain string column) so a later generation step can dispatch on a
    closed set of known kinds instead of re-parsing free text. Only
    the generated=true kinds from an approved proposal ever produce a
    row in PipelineVersionScaffoldFile - "readme" and "license" are
    deliberately absent from this enum, since those are planning-only
    and never get generated or stored as a row at all.
    """
    docker_compose = "docker_compose"
    env_example = "env_example"
    gitignore = "gitignore"
    requirements = "requirements"
    airflow_dag = "airflow_dag"
    other = "other"


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
    # All four genuinely optional, by design - captured at project
    # creation to give propose_architecture.py more to reason with
    # than the bare goal alone, but never required to create a
    # project. objectives elaborates on the goal itself; dataset_notes
    # covers semantic meaning the schema's structure alone can't
    # convey (e.g. what a status code actually means); known_constraints
    # covers operational realities (rate limits, expected duplicates);
    # load_pattern is the one genuinely structured choice among the
    # four, not free text.
    objectives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dataset_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    known_constraints: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    load_pattern: Mapped[Optional[LoadPattern]] = mapped_column(
        SAEnum(LoadPattern, name="load_pattern"), nullable=True
    )
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
    files: Mapped[list["ConnectionProfileFile"]] = relationship(
        back_populates="connection_profile", cascade="all, delete-orphan",
        order_by="ConnectionProfileFile.file_order",
    )


class ConnectionProfileFile(Base):
    """One uploaded file within a multi-file file-upload connection
    profile - the same "one row per file" pattern as
    PipelineVersionFile, so a 5-file dataset is a real, queryable list
    rather than an array packed into encrypted_credentials.

    Unlike PipelineVersionFile, there's no sequential dependency between
    files here - every file in a profile is independent and can be
    introspected/staged in any order. file_order exists purely for
    stable display ordering (files uploaded in the same request can
    share an identical created_at timestamp), not an approval gate.

    format is a plain string, not a Postgres enum, deliberately -
    format is validated in Python (connection_service.py's
    _validate_file_shape) rather than at the DB level, sidestepping the
    exact "Alembic autogenerate misses enum additions" friction this
    project has already hit twice. Adding a new supported format later
    (TSV, Parquet) needs no migration.

    connection_profiles.encrypted_credentials stays NOT NULL and holds
    an encrypted empty dict for file-type profiles - the real per-file
    data lives here instead, but every profile still decrypts through
    the same code path with no NULL-branch special case.
    """

    __tablename__ = "connection_profile_files"
    __table_args__ = (
        UniqueConstraint("connection_profile_id", "file_order", name="uq_cpf_profile_order"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    connection_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connection_profiles.id"), nullable=False, index=True
    )
    file_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # The name introspect_files() keys its output by, and what the
    # generated pipeline code references under SOURCE_FILES_DIR - must
    # be preserved exactly as uploaded, not renamed to avoid on-disk
    # collisions (storage_path handles that instead).
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connection_profile: Mapped["ConnectionProfile"] = relationship(back_populates="files")


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
    # Opt-in, not a replacement of the default flow - when True, each
    # file's destination schema (CREATE TABLE, with real columns/types/
    # constraints) must be independently proposed, reviewed, and
    # actually applied to the real destination BEFORE that file's
    # transformation code is generated, and generated code is then
    # constrained to that already-approved schema (dlt's
    # schema_contract={"columns": "freeze"}) rather than being free to
    # define its own columns on every regeneration. Set once, at
    # architecture-proposal time; every existing pipeline predates this
    # column and defaults to False, meaning completely unchanged
    # behavior - this never retroactively applies to anything already
    # built this way.
    uses_pinned_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    review_status: Mapped[PipelineVersionReviewStatus] = mapped_column(
        SAEnum(PipelineVersionReviewStatus, name="pipeline_version_review_status"),
        default=PipelineVersionReviewStatus.draft,
        nullable=False,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Deliberately separate from review_status, not folded into it.
    # review_status's "testing" value already means two different real
    # things depending on when you look - "a background task is
    # actively working on this right now" and "the last attempt
    # finished and failed, ready for a retry" - and that ambiguity was
    # always harmless while generation ran synchronously inside one
    # HTTP request, since the response itself was what told the caller
    # an attempt was over. Once generation runs in a separate Celery
    # worker, the frontend has nothing else to watch - it needs an
    # unambiguous signal for "is something running right now", not an
    # overloaded reading of a status value that already means something
    # else. Set True by the endpoint before dispatching the task, and
    # set False by the task in a finally block, so a worker crashing
    # unexpectedly can never leave a version stuck showing "in
    # progress" forever.
    generation_in_progress: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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
    scaffold_files: Mapped[list["PipelineVersionScaffoldFile"]] = relationship(
        back_populates="pipeline_version", cascade="all, delete-orphan",
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
    # Where this file conceptually sits in the exported project tree -
    # null for the project root, otherwise a folder name (e.g. "src").
    # Carried over from the approved proposal's own ProposedFile.directory
    # - purely a display/export placement hint, same as ScaffoldFile.path's
    # folder prefix. file_name itself stays a bare filename throughout;
    # generation, the sandbox, and everything that reads file_name
    # directly is untouched by this.
    directory: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    # Real schema table names and/or earlier files' destination_table
    # values - a list since a later-stage file (e.g. a gold-layer
    # aggregation) may join more than one upstream table.
    reads_from: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    destination_table: Mapped[str] = mapped_column(String(255), nullable=False)
    # All three null unless this file's version has uses_pinned_schema
    # set - the normal, unchanged case for every pipeline that predates
    # this feature and every one that doesn't opt into it.
    #
    # schema_ddl: the proposed/approved CREATE TABLE statement for this
    # file's destination_table, generated by propose_file_schema.py -
    # deliberately its own, separate AI call from generate_pipeline.py,
    # reviewed and approved on its own before any transformation code
    # exists at all.
    #
    # schema_review_status: its own review track, genuinely independent
    # of review_status below (which tracks the CODE once schema
    # approval unlocks generating it) - a file isn't done until both
    # are approved, not just one. Reuses PipelineVersionReviewStatus's
    # values but needs its own distinct Postgres enum type name, same
    # reasoning already documented on review_status just below: sharing
    # one Postgres enum type across multiple columns is a known Alembic
    # autogenerate pitfall this project has already hit once before.
    #
    # schema_applied_at: set only once schema_ddl has actually been
    # executed against the real destination - approving the schema
    # isn't just a status flip, it's the table genuinely coming into
    # existence with exactly that structure. Null here (even with
    # schema_review_status already "approved") means the CREATE TABLE
    # itself hasn't actually run yet or failed - never assumed to have
    # silently succeeded.
    schema_ddl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_review_status: Mapped[Optional[PipelineVersionReviewStatus]] = mapped_column(
        SAEnum(PipelineVersionReviewStatus, name="pipeline_version_file_schema_review_status"),
        nullable=True,
    )
    schema_applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # The latest schema-specific rejection comment - a genuinely
    # separate feedback track from the code review's own comments
    # (which live in PipelineReview, keyed by pipeline_version_file_id
    # with no way to distinguish "this was about the schema" from
    # "this was about the code" for the same file). Simpler to add this
    # one small column than to retrofit a discriminator onto
    # PipelineReview's already-shipped structure.
    schema_rejection_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    # Same reasoning and same contract as PipelineVersion.generation_in_progress.
    generation_in_progress: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_version: Mapped["PipelineVersion"] = relationship(back_populates="files")
    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="pipeline_version_file")


class PipelineVersionScaffoldFile(Base):
    """One supporting project file (docker-compose.yml, .env.example,
    an Airflow DAG, etc.) proposed alongside a version's build plan -
    created only for project_structure entries with generated=true;
    "readme"/"license" entries are planning-only and never get a row
    here at all (see ScaffoldFileType's own docstring).

    Deliberately a separate table from PipelineVersionFile rather than
    an extension of it: a scaffold file has no reads_from or
    destination_table - nothing meaningful for a docker-compose.yml or
    .gitignore to "read from" - and unlike pipeline files there's no
    file-N-depends-on-file-N-minus-1 ordering requirement, so
    file_order doesn't apply either. A shared table would mean a pile
    of nullable, pipeline-only columns on every scaffold row.
    """

    __tablename__ = "pipeline_version_scaffold_files"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    scaffold_type: Mapped[ScaffoldFileType] = mapped_column(
        SAEnum(ScaffoldFileType, name="scaffold_file_type"), nullable=False
    )
    # Null until this file has actually been generated.
    generated_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Distinct Postgres enum type name from both PipelineVersion's and
    # PipelineVersionFile's review_status columns, same reasoning as
    # PipelineVersionFile's own comment above - sharing one Postgres
    # enum type across tables is a known Alembic autogenerate pitfall.
    review_status: Mapped[PipelineVersionReviewStatus] = mapped_column(
        SAEnum(PipelineVersionReviewStatus, name="pipeline_version_scaffold_file_review_status"),
        default=PipelineVersionReviewStatus.draft,
        nullable=False,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_version: Mapped["PipelineVersion"] = relationship(back_populates="scaffold_files")


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
    # Same reasoning as pipeline_version_file_id above, for a scaffold
    # file's own approve/reject instead of a pipeline file's.
    pipeline_version_scaffold_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_version_scaffold_files.id"), nullable=True, index=True
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
