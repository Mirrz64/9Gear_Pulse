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

from models import (
    AuditLog,
    ConnectionProfile,
    ConnectionType,
    Pipeline,
    PipelineReview,
    PipelineReviewAction,
    PipelineRun,
    PipelineStatus,
    PipelineVersion,
    PipelineVersionReviewStatus,
    Project,
    RunStatus,
    Schedule,
    User,
)
from session import get_db
from connection_service import decrypt_credentials, postgres_url
from introspect import introspect_schema
from schedule_service import register_schedule

router = APIRouter(prefix="/api/v2", tags=["review gate"])


class ActorRequest(BaseModel):
    # Temporary explicit actor until Clerk/Auth0 is wired in. Replace this
    # field with the authenticated principal in the auth workstream.
    actor_id: uuid.UUID


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    auth_provider_id: str = Field(min_length=1, max_length=255)


class CreateProjectRequest(ActorRequest):
    name: str = Field(min_length=1, max_length=255)
    goal_description: str = Field(min_length=1)


class CreateConnectionProfileRequest(ActorRequest):
    name: str = Field(min_length=1, max_length=255)
    type: ConnectionType = ConnectionType.postgres
    credentials: dict[str, Any]


class CreatePipelineRequest(ActorRequest):
    project_id: uuid.UUID
    source_connection_id: uuid.UUID
    destination_connection_id: uuid.UUID
    generated_code: str = Field(min_length=1)


class GenerateRequest(ActorRequest):
    max_retries: int = Field(default=3, ge=1, le=3)


class TestResultRequest(ActorRequest):
    status: RunStatus
    log_output: Optional[str] = None
    error_output: Optional[str] = None
    row_count: Optional[int] = Field(default=None, ge=0)


class ReviewRequest(ActorRequest):
    comment: Optional[str] = Field(default=None, max_length=10_000)


class EditRequest(ActorRequest):
    generated_code: str = Field(min_length=1)


class ScheduleRequest(ActorRequest):
    cron_expression: str = Field(min_length=9, max_length=120)


def _owned_pipeline(db: Session, pipeline_id: uuid.UUID, actor_id: uuid.UUID, *, lock: bool = False) -> Pipeline:
    """Temporary ownership authorization used until the auth dependency exists."""
    if db.get(User, actor_id) is None:
        raise HTTPException(status_code=401, detail="Unknown review actor")
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


def _require_actor(db: Session, actor_id: uuid.UUID) -> None:
    if db.get(User, actor_id) is None:
        raise HTTPException(status_code=401, detail="Unknown actor")


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


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserRequest, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if existing:
        return {"id": existing.id, "email": existing.email}
    user = User(email=body.email.strip().lower(), auth_provider_id=body.auth_provider_id.strip())
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email}


@router.get("/projects")
def list_projects(actor_id: uuid.UUID, db: Session = Depends(get_db)):
    _require_actor(db, actor_id)
    projects = db.scalars(select(Project).where(Project.owner_id == actor_id).order_by(Project.created_at.desc()))
    return {"projects": [{"id": project.id, "name": project.name, "goal_description": project.goal_description,
                           "status": project.status, "created_at": project.created_at} for project in projects]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db)):
    _require_actor(db, body.actor_id)
    project = Project(owner_id=body.actor_id, name=body.name.strip(), goal_description=body.goal_description.strip())
    db.add(project)
    db.flush()
    _audit(db, body.actor_id, "project.created", "project", project.id)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "goal_description": project.goal_description}


@router.get("/connection-profiles")
def list_connection_profiles(actor_id: uuid.UUID, db: Session = Depends(get_db)):
    _require_actor(db, actor_id)
    profiles = db.scalars(select(ConnectionProfile).where(ConnectionProfile.owner_id == actor_id).order_by(ConnectionProfile.created_at.desc()))
    # Never return encrypted_credentials, even to the profile owner.
    return {"connection_profiles": [{"id": profile.id, "name": profile.name, "type": profile.type,
                                      "schema_metadata_json": profile.schema_metadata_json,
                                      "last_introspected_at": profile.last_introspected_at} for profile in profiles]}


@router.post("/connection-profiles", status_code=status.HTTP_201_CREATED)
def create_connection_profile(body: CreateConnectionProfileRequest, db: Session = Depends(get_db)):
    _require_actor(db, body.actor_id)
    cipher = _credential_cipher()
    encrypted_credentials = cipher.encrypt(json.dumps(body.credentials).encode("utf-8"))
    profile = ConnectionProfile(owner_id=body.actor_id, name=body.name.strip(), type=body.type,
                                encrypted_credentials=encrypted_credentials)
    db.add(profile)
    db.flush()
    _audit(db, body.actor_id, "connection.created", "connection_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return {"id": profile.id, "name": profile.name, "type": profile.type}


@router.post("/connection-profiles/{profile_id}/introspect")
def introspect_connection_profile(profile_id: uuid.UUID, body: ActorRequest, db: Session = Depends(get_db)):
    _require_actor(db, body.actor_id)
    profile = db.scalar(select(ConnectionProfile).where(ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == body.actor_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Connection profile not found")
    if profile.type != ConnectionType.postgres:
        raise HTTPException(status_code=422, detail="Only Postgres connection profiles are supported in v1")
    schema = introspect_schema(db_url=postgres_url(profile), sample_rows=0)
    profile.schema_metadata_json = schema
    profile.last_introspected_at = datetime.now(timezone.utc)
    _audit(db, body.actor_id, "connection.introspected", "connection_profile", profile.id)
    db.commit()
    return {"connection_profile_id": profile.id, "schema": schema}


@router.post("/pipelines", status_code=status.HTTP_201_CREATED)
def create_pipeline(body: CreatePipelineRequest, db: Session = Depends(get_db)):
    _require_actor(db, body.actor_id)
    if body.source_connection_id == body.destination_connection_id:
        raise HTTPException(status_code=422, detail="Source and destination connection profiles must differ")
    project = db.scalar(select(Project).where(Project.id == body.project_id, Project.owner_id == body.actor_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    profiles = list(db.scalars(select(ConnectionProfile.id).where(
        ConnectionProfile.id.in_([body.source_connection_id, body.destination_connection_id]),
        ConnectionProfile.owner_id == body.actor_id,
    )))
    if len(profiles) != 2:
        raise HTTPException(status_code=404, detail="One or both connection profiles were not found")
    pipeline = Pipeline(project_id=project.id, source_connection_id=body.source_connection_id,
                        destination_connection_id=body.destination_connection_id,
                        generated_code=body.generated_code, version=1, status=PipelineStatus.draft)
    db.add(pipeline)
    db.flush()
    version = PipelineVersion(pipeline_id=pipeline.id, version=1, generated_code=body.generated_code,
                              created_by=body.actor_id, review_status=PipelineVersionReviewStatus.draft)
    db.add(version)
    _audit(db, body.actor_id, "pipeline.created", "pipeline", pipeline.id)
    db.commit()
    db.refresh(pipeline)
    return {"pipeline_id": pipeline.id, "version_id": version.id, "status": pipeline.status}


@router.post("/pipelines/{pipeline_id}/generate")
def generate_and_test_pipeline(pipeline_id: uuid.UUID, body: GenerateRequest, db: Session = Depends(get_db)):
    """Generate code from cached source schema, sandbox it, and persist evidence."""
    pipeline = _owned_pipeline(db, pipeline_id, body.actor_id, lock=True)
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        raise HTTPException(status_code=409, detail="Pipeline connection profile is missing")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before generating a pipeline")

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
        _audit(db, body.actor_id, "pipeline.generation_failed", "pipeline", pipeline.id)
        db.commit()
        raise HTTPException(status_code=502, detail=f"Pipeline generation failed: {exc}") from exc
    version_number = pipeline.version + 1
    version = PipelineVersion(pipeline_id=pipeline.id, version=version_number, generated_code=code,
                              created_by=body.actor_id, review_status=PipelineVersionReviewStatus.testing)
    pipeline.version = version_number
    pipeline.generated_code = code
    pipeline.status = PipelineStatus.testing
    db.add(version)
    db.flush()
    source_url, destination_url = postgres_url(source), postgres_url(destination)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
        script.write(code)
        script_path = script.name
    try:
        from heal_pipeline import execute_with_self_healing
        success, attempts, logs = execute_with_self_healing(script_path, source.schema_metadata_json, body.max_retries, source_url, destination_url)
    finally:
        os.unlink(script_path)
    run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                      status=RunStatus.success if success else RunStatus.failed,
                      started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                      log_output=logs if success else None, error_output=None if success else logs)
    db.add(run)
    version.review_status = PipelineVersionReviewStatus.pending_review if success else PipelineVersionReviewStatus.testing
    _audit(db, body.actor_id, "pipeline_version.ready_for_review" if success else "pipeline_version.test_failed", "pipeline_version", version.id)
    db.commit()
    return {"pipeline_id": pipeline.id, "version_id": version.id, "attempts": attempts,
            "review_status": version.review_status, "sandbox_success": success}


@router.get("/pipelines/{pipeline_id}/review")
def get_review(pipeline_id: uuid.UUID, actor_id: uuid.UUID, db: Session = Depends(get_db)):
    pipeline = _owned_pipeline(db, pipeline_id, actor_id)
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
    return {
        "pipeline": {"id": pipeline.id, "status": pipeline.status, "current_version": pipeline.version},
        "version": {
            "id": current.id, "number": current.version, "code": current.generated_code,
            "review_status": current.review_status, "reviewed_by": current.reviewed_by,
            "reviewed_at": current.reviewed_at,
            # The frontend can calculate/render a diff from these two texts.
            "previous_code": previous.generated_code if previous else None,
        },
        "runs": [{"id": run.id, "status": run.status, "started_at": run.started_at,
                  "finished_at": run.finished_at, "log_output": run.log_output,
                  "error_output": run.error_output, "row_count": run.row_count} for run in runs],
        "review_history": [{"id": review.id, "actor_id": review.actor_id, "action": review.action,
                            "comment": review.comment, "created_at": review.created_at} for review in reviews],
    }


@router.post("/pipeline-versions/{version_id}/test-result", status_code=status.HTTP_201_CREATED)
def record_test_result(version_id: uuid.UUID, body: TestResultRequest, db: Session = Depends(get_db)):
    version = _owned_version(db, version_id, body.actor_id, lock=True)
    if version.review_status in {PipelineVersionReviewStatus.approved, PipelineVersionReviewStatus.rejected}:
        raise HTTPException(status_code=409, detail="Finalized versions cannot receive new test results")
    pipeline = _owned_pipeline(db, version.pipeline_id, body.actor_id, lock=True)
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
        _audit(db, body.actor_id, "pipeline_version.ready_for_review", "pipeline_version", version.id)
    else:
        _audit(db, body.actor_id, "pipeline_version.test_failed", "pipeline_version", version.id)
    db.commit()
    db.refresh(run)
    return {"run_id": run.id, "review_status": version.review_status}


@router.post("/pipeline-versions/{version_id}/approve")
def approve(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db)):
    version = _owned_version(db, version_id, body.actor_id, lock=True)
    pipeline = _owned_pipeline(db, version.pipeline_id, body.actor_id, lock=True)
    successful_run = db.scalar(select(PipelineRun.id).where(
        PipelineRun.pipeline_version_id == version.id, PipelineRun.status == RunStatus.success
    ))
    if version.version != pipeline.version:
        raise HTTPException(status_code=409, detail="Only the current pipeline version may be approved")
    if version.review_status != PipelineVersionReviewStatus.pending_review or successful_run is None:
        raise HTTPException(status_code=409, detail="A successful sandbox test is required before approval")
    version.review_status = PipelineVersionReviewStatus.approved
    version.reviewed_by = body.actor_id
    version.reviewed_at = datetime.now(timezone.utc)
    pipeline.status = PipelineStatus.approved
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=body.actor_id,
                          action=PipelineReviewAction.approved, comment=body.comment))
    _audit(db, body.actor_id, "pipeline.approved", "pipeline_version", version.id)
    db.commit()
    return {"pipeline_id": pipeline.id, "version_id": version.id, "status": "approved"}


@router.post("/pipeline-versions/{version_id}/reject")
def reject(version_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db)):
    version = _owned_version(db, version_id, body.actor_id, lock=True)
    if version.review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a version awaiting review can be rejected")
    version.review_status = PipelineVersionReviewStatus.rejected
    version.reviewed_by = body.actor_id
    version.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, actor_id=body.actor_id,
                          action=PipelineReviewAction.rejected, comment=body.comment))
    _audit(db, body.actor_id, "pipeline.rejected", "pipeline_version", version.id)
    db.commit()
    return {"version_id": version.id, "status": "rejected"}


@router.post("/pipelines/{pipeline_id}/versions", status_code=status.HTTP_201_CREATED)
def edit_pipeline(pipeline_id: uuid.UUID, body: EditRequest, db: Session = Depends(get_db)):
    pipeline = _owned_pipeline(db, pipeline_id, body.actor_id, lock=True)
    next_version = pipeline.version + 1
    version = PipelineVersion(pipeline_id=pipeline.id, version=next_version,
                              generated_code=body.generated_code, created_by=body.actor_id)
    pipeline.version = next_version
    pipeline.generated_code = body.generated_code
    pipeline.status = PipelineStatus.draft
    db.add(version)
    _audit(db, body.actor_id, "pipeline_version.created", "pipeline_version", version.id)
    db.commit()
    db.refresh(version)
    return {"version_id": version.id, "version": version.version, "review_status": version.review_status}


@router.post("/pipelines/{pipeline_id}/schedule")
def schedule_approved_pipeline(pipeline_id: uuid.UUID, body: ScheduleRequest, db: Session = Depends(get_db)):
    try:
        CronTrigger.from_crontab(body.cron_expression)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid five-field cron expression: {exc}") from exc
    pipeline = _owned_pipeline(db, pipeline_id, body.actor_id, lock=True)
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
    _audit(db, body.actor_id, "pipeline.scheduled", "pipeline", pipeline.id)
    db.commit()
    db.refresh(schedule)
    register_schedule(db, schedule)
    return {"schedule_id": schedule.id, "pipeline_version_id": version.id, "status": "scheduled"}
