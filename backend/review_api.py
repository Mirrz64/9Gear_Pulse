"""Versioned review-gate API for the PostgreSQL commercial schema.

This deliberately does not invoke generation or the scheduler. Those workers
must report their results here; this module is the authority for whether a
specific code revision may be scheduled.
"""
import uuid
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user

from models import (
    ArchitectureStatus,
    AuditLog,
    ConnectionProfile,
    ConnectionType,
    Pipeline,
    PipelineReview,
    PipelineReviewAction,
    PipelineRun,
    PipelineStatus,
    PipelineVersion,
    PipelineVersionFile,
    PipelineVersionReviewStatus,
    Project,
    RunStatus,
    Schedule,
    User,
)
from session import get_db
from connection_service import CredentialResolutionError, decrypt_credentials, postgres_url, validate_credentials_shape
from introspect import introspect_schema
from schedule_service import register_schedule

router = APIRouter(prefix="/api/v2", tags=["review gate"])

# Every request model below used to inherit an actor_id field from a
# shared ActorRequest base - that class, and the field, are gone now.
# The actor is get_current_user's verified result, never something the
# client's request body gets to assert.


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal_description: str = Field(min_length=1)


class CreateConnectionProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: ConnectionType = ConnectionType.postgres
    credentials: dict[str, Any]


class CreatePipelineRequest(BaseModel):
    project_id: uuid.UUID
    source_connection_id: uuid.UUID
    destination_connection_id: uuid.UUID
    generated_code: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=3)


class TestResultRequest(BaseModel):
    status: RunStatus
    log_output: Optional[str] = None
    error_output: Optional[str] = None
    row_count: Optional[int] = Field(default=None, ge=0)


class ReviewRequest(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=10_000)


class EditRequest(BaseModel):
    generated_code: str = Field(min_length=1)


class ScheduleRequest(BaseModel):
    cron_expression: str = Field(min_length=9, max_length=120)


def _owned_pipeline(db: Session, pipeline_id: uuid.UUID, actor_id: uuid.UUID, *, lock: bool = False) -> Pipeline:
    # The "does this actor exist" check that used to live here is gone -
    # actor_id is always current_user.id now, already a verified, just-
    # fetched real row. Checking again would be pure overhead.
    statement = (
        select(Pipeline)
        .join(Project)
        .where(Pipeline.id == pipeline_id, Project.owner_id == actor_id)
    )
    if lock:
        statement = statement.with_for_update()
    pipeline = db.scalar(statement)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return pipeline


def _credential_cipher() -> Fernet:
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="CREDENTIAL_ENCRYPTION_KEY is not configured; credentials cannot be stored safely.",
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="CREDENTIAL_ENCRYPTION_KEY is invalid.") from exc


def _owned_version(db: Session, version_id: uuid.UUID, actor_id: uuid.UUID, *, lock: bool = False) -> PipelineVersion:
    statement = select(PipelineVersion).where(PipelineVersion.id == version_id)
    if lock:
        statement = statement.with_for_update()
    version = db.scalar(statement)
    if version is None:
        raise HTTPException(status_code=404, detail="Pipeline version not found")
    _owned_pipeline(db, version.pipeline_id, actor_id, lock=lock)
    return version


def _audit(db: Session, actor_id: uuid.UUID, action: str, entity_type: str, entity_id: uuid.UUID) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id))


@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Schedule, Pipeline.version, Project.name)
        .join(Pipeline, Schedule.pipeline_id == Pipeline.id)
        .join(Project, Pipeline.project_id == Project.id)
        .where(Project.owner_id == current_user.id)
        .order_by(Schedule.next_run_at)
    ).all()
    return {"schedules": [
        {"id": schedule.id, "pipeline_id": schedule.pipeline_id, "cron_expression": schedule.cron_expression,
         "next_run_at": schedule.next_run_at, "pipeline_version": pipeline_version, "project_name": project_name}
        for schedule, pipeline_version, project_name in rows
    ]}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    projects = db.scalars(select(Project).where(Project.owner_id == current_user.id).order_by(Project.created_at.desc()))
    return {"projects": [{"id": project.id, "name": project.name, "goal_description": project.goal_description,
                           "status": project.status, "created_at": project.created_at} for project in projects]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = Project(owner_id=current_user.id, name=body.name.strip(), goal_description=body.goal_description.strip())
    db.add(project)
    db.flush()
    _audit(db, current_user.id, "project.created", "project", project.id)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "goal_description": project.goal_description}


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"id": project.id, "name": project.name, "goal_description": project.goal_description,
            "status": project.status, "created_at": project.created_at}


@router.get("/projects/{project_id}/pipelines")
def list_project_pipelines(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    pipelines = db.scalars(
        select(Pipeline).where(Pipeline.project_id == project_id).order_by(Pipeline.version.desc())
    )
    return {"pipelines": [{"id": p.id, "status": p.status, "version": p.version} for p in pipelines]}


@router.get("/connection-profiles")
def list_connection_profiles(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    profiles = db.scalars(select(ConnectionProfile).where(ConnectionProfile.owner_id == current_user.id).order_by(ConnectionProfile.created_at.desc()))
    # Never return encrypted_credentials, even to the profile owner.
    return {"connection_profiles": [{"id": profile.id, "name": profile.name, "type": profile.type,
                                      "schema_metadata_json": profile.schema_metadata_json,
                                      "last_introspected_at": profile.last_introspected_at} for profile in profiles]}


@router.post("/connection-profiles", status_code=status.HTTP_201_CREATED)
def create_connection_profile(body: CreateConnectionProfileRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        validate_credentials_shape(body.type, body.credentials)
        cipher = _credential_cipher()
        encrypted_credentials = cipher.encrypt(json.dumps(body.credentials).encode("utf-8"))
    except CredentialResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    profile = ConnectionProfile(owner_id=current_user.id, name=body.name.strip(), type=body.type,
                                encrypted_credentials=encrypted_credentials)
    db.add(profile)
    db.flush()
    _audit(db, current_user.id, "connection.created", "connection_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return {"id": profile.id, "name": profile.name, "type": profile.type}


@router.post("/connection-profiles/{profile_id}/introspect")
def introspect_connection_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # No request body needed anymore - it used to exist purely to carry
    # actor_id, which is now the verified session instead.
    profile = db.scalar(select(ConnectionProfile).where(ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Connection profile not found")
    if profile.type != ConnectionType.postgres:
        raise HTTPException(status_code=422, detail="Only Postgres connection profiles are supported in v1")
    try:
        schema = introspect_schema(db_url=postgres_url(profile), sample_rows=0)
    except CredentialResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    profile.schema_metadata_json = schema
    profile.last_introspected_at = datetime.now(timezone.utc)
    _audit(db, current_user.id, "connection.introspected", "connection_profile", profile.id)
    db.commit()
    return {"connection_profile_id": profile.id, "schema": schema}


@router.delete("/connection-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    profile = db.scalar(select(ConnectionProfile).where(
        ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id
    ))
    if profile is None:
        raise HTTPException(status_code=404, detail="Connection profile not found")
    # A profile referenced by any pipeline (past or present) can't be
    # deleted outright - that FK is load-bearing for audit/review history.
    # Refuse cleanly rather than letting the database raise an
    # IntegrityError that would surface as an unhandled 500.
    in_use = db.scalar(
        select(Pipeline.id).where(
            (Pipeline.source_connection_id == profile_id) | (Pipeline.destination_connection_id == profile_id)
        ).limit(1)
    )
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail="This connection profile is used by an existing pipeline and can't be deleted.",
        )
    _audit(db, current_user.id, "connection.deleted", "connection_profile", profile.id)
    db.delete(profile)
    db.commit()
    return None


@router.post("/pipelines", status_code=status.HTTP_201_CREATED)
def create_pipeline(body: CreatePipelineRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if body.source_connection_id == body.destination_connection_id:
        raise HTTPException(status_code=422, detail="Source and destination connection profiles must differ")
    project = db.scalar(select(Project).where(Project.id == body.project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    profiles = list(db.scalars(select(ConnectionProfile.id).where(
        ConnectionProfile.id.in_([body.source_connection_id, body.destination_connection_id]),
        ConnectionProfile.owner_id == current_user.id,
    )))
    if len(profiles) != 2:
        raise HTTPException(status_code=404, detail="One or both connection profiles were not found")
    pipeline = Pipeline(project_id=project.id, source_connection_id=body.source_connection_id,
                        destination_connection_id=body.destination_connection_id,
                        generated_code=body.generated_code, version=1, status=PipelineStatus.draft)
    db.add(pipeline)
    db.flush()
    version = PipelineVersion(pipeline_id=pipeline.id, version=1, generated_code=body.generated_code,
                              created_by=current_user.id, review_status=PipelineVersionReviewStatus.draft)
    db.add(version)
    _audit(db, current_user.id, "pipeline.created", "pipeline", pipeline.id)
    db.commit()
    db.refresh(pipeline)
    return {"pipeline_id": pipeline.id, "version_id": version.id, "status": pipeline.status}


@router.post("/pipelines/{pipeline_id}/propose-architecture")
def propose_pipeline_architecture(pipeline_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Stage A: assess the goal against the real schema and propose a plan,
    before any code exists. Can be called again to get a fresh proposal
    (e.g. after re-introspecting) - this resets the gate back to
    pending_review, requiring re-approval, rather than silently keeping
    a stale prior approval valid against a new proposal.
    """
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    if source is None:
        raise HTTPException(status_code=409, detail="Pipeline source connection profile is missing")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before proposing an architecture")

    version = db.scalar(select(PipelineVersion).where(
        PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version == pipeline.version
    ).with_for_update())
    if version is None:
        raise HTTPException(status_code=404, detail="Pipeline has no current version")
    if version.review_status != PipelineVersionReviewStatus.draft:
        raise HTTPException(
            status_code=409,
            detail="Architecture can only be proposed for a version that hasn't been generated yet",
        )

    from propose_architecture import propose_architecture
    goal = pipeline.project.goal_description
    # Scoped to this specific version's own rejections, not the whole
    # pipeline's history - safe to assume any rejection found here is an
    # architecture rejection (not a code one), since this version has
    # never left 'draft' review_status. Code review only ever happens on
    # a later version, created after generation succeeds.
    latest_rejection = db.scalar(
        select(PipelineReview)
        .where(
            PipelineReview.pipeline_version_id == version.id,
            PipelineReview.action == PipelineReviewAction.rejected,
        )
        .order_by(PipelineReview.created_at.desc())
        .limit(1)
    )
    if latest_rejection and latest_rejection.comment:
        goal = (
            f"{goal}\n\nA previous architecture proposal for this pipeline was "
            f"rejected during human review with this feedback - address it in "
            f"this proposal:\n{latest_rejection.comment}"
        )

    try:
        proposal = propose_architecture(source.schema_metadata_json, goal)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Architecture proposal failed: {exc}") from exc

    version.architecture_proposal = proposal
    version.architecture_status = ArchitectureStatus.pending_review
    _audit(db, current_user.id, "pipeline_version.architecture_proposed", "pipeline_version", version.id)
    db.commit()
    db.refresh(version)
    return {
        "version_id": version.id,
        "architecture_proposal": version.architecture_proposal,
        "architecture_status": version.architecture_status,
    }


@router.post("/pipeline-versions/{version_id}/approve-architecture")
def approve_architecture(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    version = _owned_version(db, version_id, current_user.id, lock=True)
    if version.architecture_status != ArchitectureStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only an architecture proposal awaiting review can be approved")
    version.architecture_status = ArchitectureStatus.approved

    # Stage B: the approved plan's file-by-file build order is
    # materialized as real rows right here - locked in at approval
    # time, only each file's generated_code and review_status move
    # from this point on. Empty when the proposal had no files (or was
    # a pre-Stage-B single-file plan), leaving the whole-pipeline
    # generate endpoint as the path that runs.
    proposed_files = (version.architecture_proposal or {}).get("files") or []
    for i, pf in enumerate(proposed_files, start=1):
        db.add(PipelineVersionFile(
            pipeline_version_id=version.id,
            file_order=i,
            file_name=pf["file_name"],
            purpose=pf["purpose"],
            reads_from=pf.get("reads_from", []),
            destination_table=pf["destination_table"],
        ))

    # Reuses PipelineReview/AuditLog rather than a new table - a known,
    # deliberate simplification for this first pass: nothing currently
    # distinguishes an architecture-review row from a code-review row
    # other than timing (architecture reviews always happen while
    # review_status is still 'draft'). Revisit with a review_type column
    # if that ambiguity ever actually causes confusion in practice.
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=current_user.id,
                          action=PipelineReviewAction.approved, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version.architecture_approved", "pipeline_version", version.id)
    db.commit()
    return {"version_id": version.id, "architecture_status": "approved", "file_count": len(proposed_files)}


@router.post("/pipeline-versions/{version_id}/reject-architecture")
def reject_architecture(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    version = _owned_version(db, version_id, current_user.id, lock=True)
    if version.architecture_status != ArchitectureStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only an architecture proposal awaiting review can be rejected")
    version.architecture_status = ArchitectureStatus.rejected
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=current_user.id,
                          action=PipelineReviewAction.rejected, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version.architecture_rejected", "pipeline_version", version.id)
    db.commit()
    return {"version_id": version.id, "architecture_status": "rejected"}


@router.post("/pipelines/{pipeline_id}/generate")
def generate_and_test_pipeline(pipeline_id: uuid.UUID, body: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate code from cached source schema, sandbox it, and persist evidence."""
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        raise HTTPException(status_code=409, detail="Pipeline connection profile is missing")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before generating a pipeline")

    current_version = db.scalar(select(PipelineVersion).where(
        PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version == pipeline.version
    ).with_for_update())
    if current_version is None or current_version.architecture_status != ArchitectureStatus.approved:
        # This is the actual gate Stage A exists for - code generation is
        # not allowed to run until a human has approved a real, honest
        # assessment of whether the goal matches the schema. No bypass.
        raise HTTPException(
            status_code=409,
            detail="Propose and approve an architecture for this pipeline before generating code.",
        )
    if current_version.files:
        # This version's approved plan has a real file-by-file build
        # order - generating one combined script here would bypass it
        # entirely. /pipeline-version-files/{id}/generate is the path
        # for a multi-file version, one file at a time.
        raise HTTPException(
            status_code=409,
            detail="This pipeline has a multi-file build plan - generate each file individually instead.",
        )

    # If the most recent review on this pipeline was a rejection, fold the
    # reviewer's comment into the goal context - otherwise regeneration is
    # blind to *why* the last attempt was rejected and can easily just
    # reproduce the same problem. Only the single most recent rejection is
    # used deliberately, to avoid stacking stale, possibly-contradictory
    # feedback across multiple past attempts into one prompt.
    goal = pipeline.project.goal_description
    latest_rejection = db.scalar(
        select(PipelineReview)
        .join(PipelineVersion, PipelineReview.pipeline_version_id == PipelineVersion.id)
        .where(
            PipelineVersion.pipeline_id == pipeline.id,
            PipelineReview.action == PipelineReviewAction.rejected,
        )
        .order_by(PipelineReview.created_at.desc())
        .limit(1)
    )
    if latest_rejection and latest_rejection.comment:
        goal = (
            f"{goal}\n\nA previous attempt at this pipeline was rejected during "
            f"human review with this feedback - address it in this attempt:\n"
            f"{latest_rejection.comment}"
        )

    from generate_pipeline import generate_pipeline
    try:
        generated = generate_pipeline(source.schema_metadata_json, goal)
        code = generated.get("code")
        if not code:
            raise ValueError("AI response did not include pipeline code")
    except Exception as exc:
        _audit(db, current_user.id, "pipeline.generation_failed", "pipeline", pipeline.id)
        db.commit()
        raise HTTPException(status_code=502, detail=f"Pipeline generation failed: {exc}") from exc
    version_number = pipeline.version + 1
    version = PipelineVersion(pipeline_id=pipeline.id, version=version_number, generated_code=code,
                              created_by=current_user.id, review_status=PipelineVersionReviewStatus.testing,
                              architecture_proposal=current_version.architecture_proposal,
                              architecture_status=current_version.architecture_status)
    pipeline.version = version_number
    pipeline.generated_code = code
    pipeline.status = PipelineStatus.testing
    db.add(version)
    db.flush()

    started_at = datetime.now(timezone.utc)
    try:
        source_url, destination_url = postgres_url(source), postgres_url(destination)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
            script.write(code)
            script_path = script.name
        try:
            from heal_pipeline import execute_with_self_healing
            success, attempts, logs = execute_with_self_healing(
                script_path, source.schema_metadata_json, body.max_retries, source_url, destination_url
            )
        finally:
            os.unlink(script_path)
    except CredentialResolutionError as exc:
        success, attempts = False, 0
        logs = f"Could not resolve connection credentials: {exc}"
    except Exception as exc:
        # execute_with_self_healing can raise rather than returning a
        # (False, ...) result like a normal sandbox failure - e.g.
        # heal_script's "Neither Anthropic nor OpenAI execution
        # succeeded" when both AI providers fail during a self-heal
        # attempt. Treat it the same as any other failed test rather
        # than letting the whole request crash with an unhandled 500.
        success, attempts = False, 0
        logs = f"Unexpected error during sandbox test: {exc}"

    quality_result = None
    row_count = None
    if success:
        try:
            from data_quality import run_quality_checks
            quality_result = run_quality_checks(
                destination_url,
                generated.get("destination_dataset", ""),
                generated.get("destination_table", ""),
            )
            row_count = quality_result.get("row_count")
        except Exception as exc:
            # Quality checks are informational, never load-bearing - a
            # failure here (e.g. a transient connection issue) shouldn't
            # take down an otherwise-successful test run.
            quality_result = {"checked": False, "reason": f"Quality check itself failed: {exc}"}

    run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                      status=RunStatus.success if success else RunStatus.failed,
                      started_at=started_at, finished_at=datetime.now(timezone.utc),
                      log_output=logs if success else None, error_output=None if success else logs,
                      row_count=row_count, quality_checks=quality_result)
    db.add(run)
    version.review_status = PipelineVersionReviewStatus.pending_review if success else PipelineVersionReviewStatus.testing
    _audit(db, current_user.id, "pipeline_version.ready_for_review" if success else "pipeline_version.test_failed", "pipeline_version", version.id)
    db.commit()
    return {"pipeline_id": pipeline.id, "version_id": version.id, "attempts": attempts,
            "review_status": version.review_status, "sandbox_success": success}


@router.post("/pipeline-version-files/{file_id}/generate")
def generate_and_test_file(file_id: uuid.UUID, body: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate code for ONE file in a multi-file version's approved
    build plan, sandbox-test it, and persist evidence - mirrors
    generate_and_test_pipeline exactly, scoped to a single file instead
    of a whole pipeline. execute_with_self_healing already operates on
    one script at a time internally, so it's reused here completely
    unchanged - it never mattered whether that script was "the whole
    pipeline" or "one file within it".
    """
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    version = _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)
    pipeline = _owned_pipeline(db, version.pipeline_id, current_user.id, lock=True)

    # Strict ordering: file N can only depend on file N-1's destination
    # table genuinely existing, which requires N-1 to already be
    # approved. Not just a UX nicety - this is what lets each file be
    # tested standalone against real, materialized upstream data.
    earlier_unapproved = db.scalar(
        select(PipelineVersionFile.id).where(
            PipelineVersionFile.pipeline_version_id == version.id,
            PipelineVersionFile.file_order < file.file_order,
            PipelineVersionFile.review_status != PipelineVersionReviewStatus.approved,
        ).limit(1)
    )
    if earlier_unapproved is not None:
        raise HTTPException(status_code=409, detail="An earlier file in this build plan hasn't been approved yet")
    if file.review_status not in (PipelineVersionReviewStatus.draft, PipelineVersionReviewStatus.testing, PipelineVersionReviewStatus.rejected):
        raise HTTPException(status_code=409, detail="This file has already been generated and is awaiting review, or is finalized")

    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        raise HTTPException(status_code=409, detail="Pipeline connection profile is missing")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before generating a pipeline")

    pipeline.status = PipelineStatus.testing

    try:
        source_url, destination_url = postgres_url(source), postgres_url(destination)
    except CredentialResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Files after the first may read an EARLIER file's destination
    # table - not something the original source schema knows about.
    # Introspecting the real destination here (which, by construction,
    # only contains earlier APPROVED files' actual output at this
    # point) gives generation and self-healing real column-level truth
    # instead of just a table-name reference.
    schema_for_generation = dict(source.schema_metadata_json)
    if file.file_order > 1:
        try:
            schema_for_generation.update(introspect_schema(db_url=destination_url, sample_rows=0))
        except Exception as exc:
            print(f"[Warning] Could not introspect destination for upstream file context: {exc}")

    file_goal = (
        f"{pipeline.project.goal_description}\n\n"
        f"You are generating ONE file within an approved multi-file build plan: "
        f"'{file.file_name}'. Purpose: {file.purpose}. This file must read ONLY "
        f"from: {', '.join(file.reads_from) or '(the original source only)'}. "
        f"Write its output to a table named exactly '{file.destination_table}'. "
        f"Do not implement any other file's purpose - only this one."
    )
    latest_rejection = db.scalar(
        select(PipelineReview)
        .where(
            PipelineReview.pipeline_version_file_id == file.id,
            PipelineReview.action == PipelineReviewAction.rejected,
        )
        .order_by(PipelineReview.created_at.desc())
        .limit(1)
    )
    if latest_rejection and latest_rejection.comment:
        file_goal = (
            f"{file_goal}\n\nA previous attempt at this specific file was rejected "
            f"during human review with this feedback - address it in this attempt:\n"
            f"{latest_rejection.comment}"
        )

    from generate_pipeline import generate_pipeline
    try:
        generated = generate_pipeline(schema_for_generation, file_goal)
        code = generated.get("code")
        if not code:
            raise ValueError("AI response did not include pipeline code")
    except Exception as exc:
        _audit(db, current_user.id, "pipeline_version_file.generation_failed", "pipeline_version_file", file.id)
        db.commit()
        raise HTTPException(status_code=502, detail=f"File generation failed: {exc}") from exc

    file.generated_code = code
    file.review_status = PipelineVersionReviewStatus.testing
    db.flush()

    started_at = datetime.now(timezone.utc)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
            script.write(code)
            script_path = script.name
        try:
            from heal_pipeline import execute_with_self_healing
            success, attempts, logs = execute_with_self_healing(
                script_path, schema_for_generation, body.max_retries, source_url, destination_url
            )
        finally:
            os.unlink(script_path)
    except Exception as exc:
        success, attempts = False, 0
        logs = f"Unexpected error during sandbox test: {exc}"

    quality_result = None
    row_count = None
    if success:
        try:
            from data_quality import run_quality_checks
            quality_result = run_quality_checks(
                destination_url, generated.get("destination_dataset", ""), file.destination_table,
            )
            row_count = quality_result.get("row_count")
        except Exception as exc:
            quality_result = {"checked": False, "reason": f"Quality check itself failed: {exc}"}

    run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                      pipeline_version_file_id=file.id,
                      status=RunStatus.success if success else RunStatus.failed,
                      started_at=started_at, finished_at=datetime.now(timezone.utc),
                      log_output=logs if success else None, error_output=None if success else logs,
                      row_count=row_count, quality_checks=quality_result)
    db.add(run)
    file.review_status = PipelineVersionReviewStatus.pending_review if success else PipelineVersionReviewStatus.testing
    _audit(db, current_user.id,
           "pipeline_version_file.ready_for_review" if success else "pipeline_version_file.test_failed",
           "pipeline_version_file", file.id)
    db.commit()
    return {"file_id": file.id, "attempts": attempts, "review_status": file.review_status, "sandbox_success": success}


@router.post("/pipeline-version-files/{file_id}/approve")
def approve_file(file_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    version = _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)

    successful_run = db.scalar(select(PipelineRun.id).where(
        PipelineRun.pipeline_version_file_id == file.id, PipelineRun.status == RunStatus.success
    ))
    if file.review_status != PipelineVersionReviewStatus.pending_review or successful_run is None:
        raise HTTPException(status_code=409, detail="A successful sandbox test is required before approving this file")

    file.review_status = PipelineVersionReviewStatus.approved
    file.reviewed_by = current_user.id
    file.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, pipeline_version_file_id=file.id,
                          actor_id=current_user.id, action=PipelineReviewAction.approved, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version_file.approved", "pipeline_version_file", file.id)

    # Without this, the remaining-files check below can run before this
    # file's own status change has actually reached the database (it
    # selects a bare id column, not the full ORM row, so it queries the
    # database directly rather than seeing the in-memory change) -
    # incorrectly finding THIS file as still "remaining" and never
    # flipping the whole version once it was genuinely the last one.
    db.flush()

    # If this was the last unapproved file, the whole version is ready
    # for final sign-off - mirrors generate_and_test_pipeline's own
    # success path (review_status -> pending_review). Concatenating
    # every file's code into generated_code means the existing whole-
    # version approve/schedule endpoints (unchanged) have something
    # real to display and pin, without needing to know anything about
    # multi-file versions themselves.
    remaining = db.scalar(
        select(PipelineVersionFile.id).where(
            PipelineVersionFile.pipeline_version_id == version.id,
            PipelineVersionFile.review_status != PipelineVersionReviewStatus.approved,
        ).limit(1)
    )
    if remaining is None:
        all_files = list(db.scalars(
            select(PipelineVersionFile).where(PipelineVersionFile.pipeline_version_id == version.id)
            .order_by(PipelineVersionFile.file_order)
        ))
        version.generated_code = "\n\n".join(
            f"# --- {f.file_name} ({f.purpose}) ---\n{f.generated_code}" for f in all_files
        )
        version.review_status = PipelineVersionReviewStatus.pending_review
        version.pipeline.generated_code = version.generated_code

    db.commit()
    return {"file_id": file.id, "review_status": "approved", "all_files_approved": remaining is None}


@router.post("/pipeline-version-files/{file_id}/reject")
def reject_file(file_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    version = _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)
    if file.review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a file awaiting review can be rejected")
    file.review_status = PipelineVersionReviewStatus.rejected
    file.reviewed_by = current_user.id
    file.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, pipeline_version_file_id=file.id,
                          actor_id=current_user.id, action=PipelineReviewAction.rejected, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version_file.rejected", "pipeline_version_file", file.id)
    db.commit()
    return {"file_id": file.id, "review_status": "rejected"}


@router.get("/pipelines/{pipeline_id}/review")
def get_review(pipeline_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id)
    versions = list(db.scalars(
        select(PipelineVersion).where(PipelineVersion.pipeline_id == pipeline.id).order_by(PipelineVersion.version.desc())
    ))
    if not versions:
        raise HTTPException(status_code=404, detail="Pipeline has no versions to review")
    current = versions[0]
    previous = versions[1] if len(versions) > 1 else None
    runs = list(db.scalars(
        select(PipelineRun).where(PipelineRun.pipeline_version_id == current.id).order_by(PipelineRun.started_at.desc())
    ))
    reviews = list(db.scalars(
        select(PipelineReview).where(PipelineReview.pipeline_version_id == current.id).order_by(PipelineReview.created_at.desc())
    ))
    files = list(db.scalars(
        select(PipelineVersionFile).where(PipelineVersionFile.pipeline_version_id == current.id)
        .order_by(PipelineVersionFile.file_order)
    ))
    files_payload = []
    for f in files:
        file_runs = list(db.scalars(
            select(PipelineRun).where(PipelineRun.pipeline_version_file_id == f.id).order_by(PipelineRun.started_at.desc())
        ))
        files_payload.append({
            "id": f.id, "file_order": f.file_order, "file_name": f.file_name,
            "purpose": f.purpose, "reads_from": f.reads_from,
            "destination_table": f.destination_table, "generated_code": f.generated_code,
            "review_status": f.review_status,
            "runs": [{"id": r.id, "status": r.status, "started_at": r.started_at,
                      "finished_at": r.finished_at, "log_output": r.log_output,
                      "error_output": r.error_output, "row_count": r.row_count,
                      "quality_checks": r.quality_checks} for r in file_runs],
        })
    return {
        "pipeline": {"id": pipeline.id, "status": pipeline.status, "current_version": pipeline.version},
        "version": {
            "id": current.id, "number": current.version, "code": current.generated_code,
            "review_status": current.review_status, "reviewed_by": current.reviewed_by,
            "reviewed_at": current.reviewed_at,
            "architecture_proposal": current.architecture_proposal,
            "architecture_status": current.architecture_status,
            # The frontend can calculate/render a diff from these two texts.
            "previous_code": previous.generated_code if previous else None,
        },
        "runs": [{"id": run.id, "status": run.status, "started_at": run.started_at,
                  "finished_at": run.finished_at, "log_output": run.log_output,
                  "error_output": run.error_output, "row_count": run.row_count,
                  "quality_checks": run.quality_checks} for run in runs],
        "review_history": [{"id": review.id, "actor_id": review.actor_id, "action": review.action,
                            "comment": review.comment, "created_at": review.created_at} for review in reviews],
        "files": files_payload,
    }


@router.post("/pipeline-versions/{version_id}/test-result", status_code=status.HTTP_201_CREATED)
def record_test_result(version_id: uuid.UUID, body: TestResultRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    version = _owned_version(db, version_id, current_user.id, lock=True)
    if version.review_status in {PipelineVersionReviewStatus.approved, PipelineVersionReviewStatus.rejected}:
        raise HTTPException(status_code=409, detail="Finalized versions cannot receive new test results")
    pipeline = _owned_pipeline(db, version.pipeline_id, current_user.id, lock=True)
    version.review_status = PipelineVersionReviewStatus.testing
    pipeline.status = PipelineStatus.testing
    run = PipelineRun(
        pipeline_id=pipeline.id, pipeline_version_id=version.id, status=body.status,
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
        log_output=body.log_output, error_output=body.error_output, row_count=body.row_count,
    )
    db.add(run)
    if body.status == RunStatus.success:
        version.review_status = PipelineVersionReviewStatus.pending_review
        _audit(db, current_user.id, "pipeline_version.ready_for_review", "pipeline_version", version.id)
    else:
        _audit(db, current_user.id, "pipeline_version.test_failed", "pipeline_version", version.id)
    db.commit()
    db.refresh(run)
    return {"run_id": run.id, "review_status": version.review_status}


@router.post("/pipeline-versions/{version_id}/approve")
def approve(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    version = _owned_version(db, version_id, current_user.id, lock=True)
    pipeline = _owned_pipeline(db, version.pipeline_id, current_user.id, lock=True)
    successful_run = db.scalar(select(PipelineRun.id).where(
        PipelineRun.pipeline_version_id == version.id, PipelineRun.status == RunStatus.success
    ))
    if version.version != pipeline.version:
        raise HTTPException(status_code=409, detail="Only the current pipeline version may be approved")
    if version.review_status != PipelineVersionReviewStatus.pending_review or successful_run is None:
        raise HTTPException(status_code=409, detail="A successful sandbox test is required before approval")
    version.review_status = PipelineVersionReviewStatus.approved
    version.reviewed_by = current_user.id
    version.reviewed_at = datetime.now(timezone.utc)
    pipeline.status = PipelineStatus.approved
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=current_user.id,
                          action=PipelineReviewAction.approved, comment=body.comment))
    _audit(db, current_user.id, "pipeline.approved", "pipeline_version", version.id)
    db.commit()
    return {"pipeline_id": pipeline.id, "version_id": version.id, "status": "approved"}


@router.post("/pipeline-versions/{version_id}/reject")
def reject(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    version = _owned_version(db, version_id, current_user.id, lock=True)
    if version.review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a version awaiting review can be rejected")
    version.review_status = PipelineVersionReviewStatus.rejected
    version.reviewed_by = current_user.id
    version.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=current_user.id,
                          action=PipelineReviewAction.rejected, comment=body.comment))
    _audit(db, current_user.id, "pipeline.rejected", "pipeline_version", version.id)
    db.commit()
    return {"version_id": version.id, "status": "rejected"}


@router.post("/pipelines/{pipeline_id}/versions", status_code=status.HTTP_201_CREATED)
def edit_pipeline(pipeline_id: uuid.UUID, body: EditRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    next_version = pipeline.version + 1
    version = PipelineVersion(pipeline_id=pipeline.id, version=next_version,
                              generated_code=body.generated_code, created_by=current_user.id)
    pipeline.version = next_version
    pipeline.generated_code = body.generated_code
    pipeline.status = PipelineStatus.draft
    db.add(version)
    _audit(db, current_user.id, "pipeline_version.created", "pipeline_version", version.id)
    db.commit()
    db.refresh(version)
    return {"version_id": version.id, "version": version.version, "review_status": version.review_status}


@router.post("/pipelines/{pipeline_id}/schedule")
def schedule_approved_pipeline(pipeline_id: uuid.UUID, body: ScheduleRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        CronTrigger.from_crontab(body.cron_expression)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid five-field cron expression: {exc}") from exc
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    version = db.scalar(select(PipelineVersion).where(
        PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version == pipeline.version
    ))
    if pipeline.status != PipelineStatus.approved or version is None or version.review_status != PipelineVersionReviewStatus.approved:
        raise HTTPException(status_code=409, detail="The current pipeline version must be approved before scheduling")
    schedule = db.scalar(select(Schedule).where(Schedule.pipeline_id == pipeline.id).with_for_update())
    if schedule is None:
        schedule = Schedule(pipeline_id=pipeline.id, pipeline_version_id=version.id, cron_expression=body.cron_expression)
        db.add(schedule)
    else:
        schedule.pipeline_version_id = version.id
        schedule.cron_expression = body.cron_expression
    pipeline.status = PipelineStatus.scheduled
    _audit(db, current_user.id, "pipeline.scheduled", "pipeline", pipeline.id)
    db.commit()
    db.refresh(schedule)
    register_schedule(db, schedule)
    return {"schedule_id": schedule.id, "pipeline_version_id": version.id, "status": "scheduled"}
