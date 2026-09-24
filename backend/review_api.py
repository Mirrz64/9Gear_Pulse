"""Versioned review-gate API for the PostgreSQL commercial schema.

This deliberately does not invoke generation or the scheduler. Those workers
must report their results here; this module is the authority for whether a
specific code revision may be scheduled.
"""
import uuid
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
import requests
import redis
from datetime import datetime, timezone
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from auth import get_current_user

from models import (
    ArchitectureStatus,
    AuditLog,
    ConnectionProfile,
    ConnectionProfileFile,
    ConnectionType,
    LoadPattern,
    Pipeline,
    PipelineReview,
    PipelineReviewAction,
    PipelineRun,
    PipelineStatus,
    PipelineVersion,
    PipelineVersionFile,
    PipelineVersionReviewStatus,
    PipelineVersionScaffoldFile,
    Project,
    RunStatus,
    Schedule,
    ScaffoldFileType,
    User,
)
from session import get_db
from connection_service import (
    CredentialResolutionError, api_credentials, azure_sql_schema_name, azure_sql_url, decrypt_credentials, file_credentials,
    graphql_credentials, postgres_url, postgres_schema_name, redis_url, soap_credentials, validate_credentials_shape,
)
from introspect import introspect_api, introspect_files, introspect_graphql, introspect_redis, introspect_schema, introspect_soap
from schedule_service import job_id, register_schedule, scheduler
from apscheduler.jobstores.base import JobLookupError

router = APIRouter(prefix="/api/v2", tags=["review gate"])

# Every request model below used to inherit an actor_id field from a
# shared ActorRequest base - that class, and the field, are gone now.
# The actor is get_current_user's verified result, never something the
# client's request body gets to assert.


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal_description: str = Field(min_length=1)
    # All four genuinely optional - see Project model's own docstring
    # for what each is for. load_pattern typed as the real enum (not
    # a bare str) so an invalid value is rejected with a clear 422 at
    # the API boundary, the same way every other enum-typed field in
    # this file already works.
    objectives: Optional[str] = None
    dataset_notes: Optional[str] = None
    known_constraints: Optional[str] = None
    load_pattern: Optional[LoadPattern] = None


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    goal_description: Optional[str] = Field(default=None, min_length=1)


class CreateConnectionProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: ConnectionType = ConnectionType.postgres
    credentials: dict[str, Any]


class UpdateConnectionProfileRequest(BaseModel):
    """Type is deliberately not editable here - changing what kind of
    profile this is (e.g. postgres -> api) means a fundamentally
    different credentials shape and, for a file profile, a completely
    different storage model (see file_credentials()'s own docstring in
    connection_service.py). That's a new profile, not an edit to an
    existing one.

    credentials is a PARTIAL patch, merged onto the existing decrypted
    dict rather than replacing it outright - callers only send the
    field(s) actually changing (e.g. just "password" to rotate one),
    not the full credentials shape every time. A key can be explicitly
    overwritten to empty/null this way (e.g. credentials={"auth_headers":
    {}} to clear API auth, or {"schema": null} to fall back to public) -
    what a merge can't do is remove a key entirely and fall back to
    some other default; that's a real but narrow limitation.
    """
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    credentials: Optional[dict[str, Any]] = None


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


def _split_destination_table(dataset: str, destination_table: str) -> tuple[str, str]:
    """data_quality.py's run_quality_checks(dataset, table) expects both
    as separate bare strings - it checks `table` against
    inspector.get_table_names(schema=dataset), which only ever returns
    bare names, never schema-qualified ones. But destination_table (both
    the AI's own reported GeneratedPipeline.destination_table and the
    PipelineVersionFile.destination_table column derived from it -
    confirmed directly via psql to always be stored as "schema.table")
    already carries the schema prefix. Passing both as-is meant "table"
    could never match, and the resulting not-found reason string
    doubled the schema: "silver.silver.x" - dataset ("silver") plus an
    already-qualified table ("silver.x").

    This splits destination_table on its own prefix when present,
    ignoring dataset in that case, so the two callers stop needing to
    know or care whether destination_table happens to include the
    schema - it works out cleanly either way.
    """
    if destination_table and "." in destination_table:
        schema, table = destination_table.split(".", 1)
        return schema, table
    return dataset, destination_table


def _validate_scaffold_content(scaffold_type: str, content: str) -> Optional[str]:
    """Returns None if content passes validation for this scaffold_type,
    or a human-readable reason string if it doesn't. Deliberately
    light-touch: a scaffold file has no real "run" step the way
    pipeline code does (no sandbox to execute a docker-compose.yml or
    a requirements.txt against), so this only catches genuinely broken
    output - invalid YAML, invalid Python syntax - not anything more
    opinionated about the file's actual content.
    """
    if not content or not content.strip():
        return "Generated content is empty."
    if scaffold_type == "docker_compose":
        try:
            import yaml
            yaml.safe_load(content)
        except Exception as exc:
            return f"Not valid YAML: {exc}"
    elif scaffold_type == "airflow_dag":
        import ast
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return f"Not valid Python syntax: {exc}"
    # env_example, gitignore, requirements, other: no further check -
    # plain text with no meaningful syntax beyond "not empty", already
    # covered above.
    return None


def _uploads_base_dir() -> str:
    # UPLOADS_DIR is set explicitly in docker-compose.yml's backend
    # service (pointing at the shared uploads_data volume); this
    # process runs as bare `uvicorn` in real day-to-day dev, though,
    # where that env var won't be set - fall back to a real local
    # directory next to this file rather than a container path that
    # doesn't exist here, same "config supplies what's right for its
    # own context" approach DATABASE_URL/PG_HOST already use.
    configured = os.environ.get("UPLOADS_DIR")
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")


# _resolve_source_execution_params used to live here, but nothing in this
# file has called it since generation moved into Celery tasks
# (generation_tasks.py) - the active, only-remaining copy lives there now.
# Removed rather than left in place unused, since dead code that looks
# active is exactly what leads to wasted maintenance on a branch nothing
# ever executes - confirmed directly by grep before removing this: zero
# call sites anywhere in this file, only the definition itself.


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


def _derive_project_category(pipelines: list["Pipeline"]) -> str:
    """A separate, additional dimension from Project.status (active/
    archived, which tracks whether the project is still in use at all)
    - this tracks where the project actually stands in its own build
    lifecycle. Deliberately NOT stored - computed fresh from each
    pipeline's own real status every time a project is read, rather
    than cached on the Project row, since a cached value would need
    updating in every single endpoint that changes a pipeline's status
    (propose-architecture, generate-and-test, approve, schedule,
    unschedule...) to stay correct, with a real, ongoing risk of
    drifting stale in whichever one gets missed.

    "Highest stage any pipeline has reached wins" - a project with one
    draft pipeline and one approved pipeline shows as completed,
    reflecting its most advanced real work, not its least.
    """
    statuses = {p.status for p in pipelines}
    if PipelineStatus.approved in statuses or PipelineStatus.scheduled in statuses:
        return "completed"
    if PipelineStatus.testing in statuses:
        return "testing"
    return "new"


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    projects = db.scalars(
        select(Project).where(Project.owner_id == current_user.id)
        .options(selectinload(Project.pipelines))
        .order_by(Project.created_at.desc())
    )
    return {"projects": [{"id": project.id, "name": project.name, "goal_description": project.goal_description,
                           "status": project.status, "category": _derive_project_category(project.pipelines),
                           "created_at": project.created_at} for project in projects]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = Project(
        owner_id=current_user.id,
        name=body.name.strip(),
        goal_description=body.goal_description.strip(),
        objectives=body.objectives.strip() if body.objectives and body.objectives.strip() else None,
        dataset_notes=body.dataset_notes.strip() if body.dataset_notes and body.dataset_notes.strip() else None,
        known_constraints=body.known_constraints.strip() if body.known_constraints and body.known_constraints.strip() else None,
        load_pattern=body.load_pattern,
    )
    db.add(project)
    db.flush()
    _audit(db, current_user.id, "project.created", "project", project.id)
    db.commit()
    db.refresh(project)
    return {
        "id": project.id, "name": project.name, "goal_description": project.goal_description,
        "objectives": project.objectives, "dataset_notes": project.dataset_notes,
        "known_constraints": project.known_constraints, "load_pattern": project.load_pattern,
    }


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
        .options(selectinload(Project.pipelines))
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"id": project.id, "name": project.name, "goal_description": project.goal_description,
            "objectives": project.objectives, "dataset_notes": project.dataset_notes,
            "known_constraints": project.known_constraints, "load_pattern": project.load_pattern,
            "status": project.status, "category": _derive_project_category(project.pipelines),
            "created_at": project.created_at}


@router.get("/projects/{project_id}/pipelines")
def list_project_pipelines(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    pipelines = db.scalars(
        select(Pipeline).where(Pipeline.project_id == project_id).order_by(Pipeline.version.desc())
    )
    return {"pipelines": [{"id": p.id, "status": p.status, "version": p.version} for p in pipelines]}


@router.patch("/projects/{project_id}")
def update_project(project_id: uuid.UUID, body: UpdateProjectRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.name is None and body.goal_description is None:
        raise HTTPException(status_code=422, detail="Provide at least one of name or goal_description to update.")
    if body.name is not None:
        project.name = body.name.strip()
    if body.goal_description is not None:
        project.goal_description = body.goal_description.strip()
    # updated_at is not set here - Project.updated_at already has
    # onupdate=func.now() at the model level, so any tracked change to
    # this row updates it automatically.
    _audit(db, current_user.id, "project.updated", "project", project.id)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "goal_description": project.goal_description, "status": project.status}


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == current_user.id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Same refuse-rather-than-cascade philosophy as delete_connection_profile,
    # for an even stronger reason here: a project's pipeline can have a
    # LIVE schedule - a real APScheduler job hitting a real destination
    # on a cron. Silently cascading a delete through that would leave an
    # orphaned job still firing against a pipeline_version_id that no
    # longer resolves to anything, not just lose review/audit history.
    pipeline_count = db.scalar(select(func.count(Pipeline.id)).where(Pipeline.project_id == project_id))
    if pipeline_count:
        raise HTTPException(
            status_code=409,
            detail=f"This project has {pipeline_count} pipeline(s). Delete or reassign them first.",
        )
    _audit(db, current_user.id, "project.deleted", "project", project.id)
    db.delete(project)
    db.commit()


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


@router.post("/connection-profiles/file", status_code=status.HTTP_201_CREATED)
async def create_file_connection_profile(
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Multipart counterpart to create_connection_profile() - a file
    source has real bytes to send, not JSON-serializable credentials,
    so this is a genuinely separate endpoint rather than a branch
    inside the JSON one.

    encrypted_credentials still gets populated (an encrypted empty
    dict) purely so every profile decrypts through the same code path;
    the real per-file data goes straight into connection_profile_files,
    never through encryption at all - see file_credentials() and
    ConnectionProfileFile's docstring in models.py.
    """
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required.")

    format_by_extension = {".csv": "csv", ".json": "json"}

    try:
        cipher = _credential_cipher()
        encrypted_credentials = cipher.encrypt(json.dumps({}).encode("utf-8"))
    except CredentialResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    profile = ConnectionProfile(owner_id=current_user.id, name=name.strip(),
                                type=ConnectionType.file, encrypted_credentials=encrypted_credentials)
    db.add(profile)
    db.flush()

    profile_dir = os.path.join(_uploads_base_dir(), str(profile.id))
    os.makedirs(profile_dir, exist_ok=True)

    for order, upload in enumerate(files, start=1):
        # Never trust a client-supplied filename directly in a server-
        # side path - basename strips any directory components (e.g.
        # "../../etc/passwd") that could otherwise escape profile_dir.
        safe_filename = os.path.basename(upload.filename or "")
        if not safe_filename or safe_filename in (".", ".."):
            raise HTTPException(status_code=422, detail="Invalid file name.")
        _, ext = os.path.splitext(safe_filename)
        file_format = format_by_extension.get(ext.lower())
        if file_format is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type '{safe_filename}' - only .csv and .json are supported.",
            )
        contents = await upload.read()
        storage_path = os.path.abspath(os.path.join(profile_dir, safe_filename))
        with open(storage_path, "wb") as f:
            f.write(contents)
        db.add(ConnectionProfileFile(
            connection_profile_id=profile.id, file_order=order,
            original_filename=safe_filename, storage_path=storage_path,
            format=file_format, size_bytes=len(contents),
        ))

    _audit(db, current_user.id, "connection.created", "connection_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return {
        "id": profile.id, "name": profile.name, "type": profile.type,
        "files": [{"original_filename": f.original_filename, "format": f.format, "size_bytes": f.size_bytes}
                  for f in sorted(profile.files, key=lambda pf: pf.file_order)],
    }


@router.patch("/connection-profiles/{profile_id}")
def update_connection_profile(profile_id: uuid.UUID, body: UpdateConnectionProfileRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Postgres, API, Redis, GraphQL, and SOAP profiles use credentials
    here; a file profile's real data lives in connection_profile_files
    instead, so credentials has no meaning for one - only name-only
    renames are allowed through this endpoint for a file profile.
    Adding, removing, or replacing uploaded files is a genuinely
    different, multipart operation - see the dedicated
    /connection-profiles/{id}/files endpoints below.
    """
    profile = db.scalar(select(ConnectionProfile).where(ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Connection profile not found")
    if profile.type == ConnectionType.file and body.credentials is not None:
        raise HTTPException(status_code=422, detail="File connection profiles have no credentials to update - add, remove, or replace files on the profile instead.")
    if body.name is None and body.credentials is None:
        raise HTTPException(status_code=422, detail="Provide at least one of name or credentials to update.")

    if body.name is not None:
        profile.name = body.name.strip()

    if body.credentials is not None:
        try:
            merged = {**decrypt_credentials(profile), **body.credentials}
            validate_credentials_shape(profile.type, merged)
            cipher = _credential_cipher()
            profile.encrypted_credentials = cipher.encrypt(json.dumps(merged).encode("utf-8"))
        except CredentialResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Credentials changed - the cached schema no longer necessarily
        # describes the real source (a different host/database/base_url
        # could point somewhere completely different). Clear it rather
        # than risk a stale schema silently being used against what's
        # now a different actual source, and so the UI correctly shows
        # "not yet introspected" instead of misleadingly-still-populated
        # old data.
        profile.schema_metadata_json = None
        profile.last_introspected_at = None

    _audit(db, current_user.id, "connection.updated", "connection_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return {"id": profile.id, "name": profile.name, "type": profile.type,
            "schema_metadata_json": profile.schema_metadata_json, "last_introspected_at": profile.last_introspected_at}


_FILE_FORMAT_BY_EXTENSION = {".csv": "csv", ".json": "json"}


@router.get("/connection-profiles/{profile_id}/files")
def list_connection_profile_files(profile_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Deliberately not folded into list_connection_profiles - that
    endpoint runs on every page load/refresh for every profile
    regardless of type, and most profiles aren't file-type at all;
    fetched here instead, on demand, only when a file profile's own
    management section is actually opened.
    """
    profile = db.scalar(select(ConnectionProfile).where(
        ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id, ConnectionProfile.type == ConnectionType.file,
    ))
    if profile is None:
        raise HTTPException(status_code=404, detail="File connection profile not found")
    files = db.scalars(
        select(ConnectionProfileFile).where(ConnectionProfileFile.connection_profile_id == profile.id).order_by(ConnectionProfileFile.file_order)
    )
    return {"files": [{"id": f.id, "original_filename": f.original_filename, "format": f.format, "size_bytes": f.size_bytes} for f in files]}


def _safe_uploaded_filename(raw_filename: Optional[str]) -> str:
    """Shared with create_file_connection_profile's own inline version -
    os.path.basename strips any directory components (e.g.
    "../../etc/passwd") that could otherwise escape the profile's own
    upload directory.
    """
    safe_filename = os.path.basename(raw_filename or "")
    if not safe_filename or safe_filename in (".", ".."):
        raise HTTPException(status_code=422, detail="Invalid file name.")
    return safe_filename


@router.post("/connection-profiles/{profile_id}/files")
async def add_connection_profile_files(
    profile_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Adds one or more new files to an existing file-type connection
    profile - the genuinely missing piece create_file_connection_profile
    itself flagged (that endpoint only ever handles a brand-new
    profile's very first upload).

    Rejects a duplicate original_filename outright rather than silently
    overwriting - uploading a same-named file would otherwise write new
    bytes to the exact same storage_path an existing
    ConnectionProfileFile row already points at, corrupting that row's
    real meaning without ever updating it. Use the replace endpoint
    below for "I have a new version of this exact file" instead.
    """
    profile = db.scalar(select(ConnectionProfile).where(
        ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id, ConnectionProfile.type == ConnectionType.file,
    ))
    if profile is None:
        raise HTTPException(status_code=404, detail="File connection profile not found")
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required.")

    existing_names = set(db.scalars(
        select(ConnectionProfileFile.original_filename).where(ConnectionProfileFile.connection_profile_id == profile.id)
    ))
    next_order = (db.scalar(
        select(func.max(ConnectionProfileFile.file_order)).where(ConnectionProfileFile.connection_profile_id == profile.id)
    ) or 0) + 1

    profile_dir = os.path.join(_uploads_base_dir(), str(profile.id))
    os.makedirs(profile_dir, exist_ok=True)

    added = []
    for upload in files:
        safe_filename = _safe_uploaded_filename(upload.filename)
        if safe_filename in existing_names:
            raise HTTPException(status_code=409, detail=f"A file named '{safe_filename}' already exists on this profile - use the replace endpoint instead.")
        _, ext = os.path.splitext(safe_filename)
        file_format = _FILE_FORMAT_BY_EXTENSION.get(ext.lower())
        if file_format is None:
            raise HTTPException(status_code=422, detail=f"Unsupported file type '{safe_filename}' - only .csv and .json are supported.")
        contents = await upload.read()
        storage_path = os.path.abspath(os.path.join(profile_dir, safe_filename))
        with open(storage_path, "wb") as f:
            f.write(contents)
        record = ConnectionProfileFile(
            connection_profile_id=profile.id, file_order=next_order,
            original_filename=safe_filename, storage_path=storage_path,
            format=file_format, size_bytes=len(contents),
        )
        db.add(record)
        added.append(record)
        existing_names.add(safe_filename)
        next_order += 1

    # The cached schema could now be stale - a new file changes what
    # this source actually contains, same principle as a credential
    # change clearing it for every other connection type above.
    profile.schema_metadata_json = None
    profile.last_introspected_at = None
    _audit(db, current_user.id, "connection.files_added", "connection_profile", profile.id)
    db.commit()
    return {"profile_id": profile.id, "added_files": [f.original_filename for f in added]}


@router.put("/connection-profiles/{profile_id}/files/{file_id}")
async def replace_connection_profile_file(
    profile_id: uuid.UUID,
    file_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replaces one existing file's real content in place - the same
    logical file, new bytes. Keeps the EXISTING row's own
    original_filename and storage_path regardless of whatever filename
    the upload itself happens to have, deliberately - a genuinely
    different name would mean a genuinely different file (add it as a
    new one instead), and silently renaming here risks an orphaned
    on-disk path or a storage_path/original_filename pair that no
    longer actually matches.
    """
    profile = db.scalar(select(ConnectionProfile).where(
        ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id, ConnectionProfile.type == ConnectionType.file,
    ))
    if profile is None:
        raise HTTPException(status_code=404, detail="File connection profile not found")
    record = db.scalar(select(ConnectionProfileFile).where(
        ConnectionProfileFile.id == file_id, ConnectionProfileFile.connection_profile_id == profile.id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="File not found on this profile")

    _, ext = os.path.splitext(record.original_filename)
    file_format = _FILE_FORMAT_BY_EXTENSION.get(ext.lower())
    if file_format is None:
        # Genuinely shouldn't happen (every existing row was itself
        # validated against this same extension list at upload time),
        # but never silently trust that invariant holds forever.
        raise HTTPException(status_code=422, detail=f"'{record.original_filename}' has an unsupported extension - this shouldn't be possible, contact support.")

    contents = await file.read()
    with open(record.storage_path, "wb") as f:
        f.write(contents)
    record.size_bytes = len(contents)

    profile.schema_metadata_json = None
    profile.last_introspected_at = None
    _audit(db, current_user.id, "connection.file_replaced", "connection_profile", profile.id)
    db.commit()
    return {"profile_id": profile.id, "file_id": record.id, "original_filename": record.original_filename, "size_bytes": record.size_bytes}


@router.delete("/connection-profiles/{profile_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_connection_profile_file(
    profile_id: uuid.UUID,
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refuses to remove the last remaining file on a profile - a file
    profile needs at least one, the same invariant
    create_file_connection_profile already enforces at creation time
    (it requires `files` to be non-empty). Disk cleanup happens after
    the commit, not before, mirroring delete_connection_profile's own
    reasoning exactly: if the commit somehow failed, the file should
    still be there to match the row that's still there.
    """
    profile = db.scalar(select(ConnectionProfile).where(
        ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id, ConnectionProfile.type == ConnectionType.file,
    ))
    if profile is None:
        raise HTTPException(status_code=404, detail="File connection profile not found")
    record = db.scalar(select(ConnectionProfileFile).where(
        ConnectionProfileFile.id == file_id, ConnectionProfileFile.connection_profile_id == profile.id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="File not found on this profile")

    remaining_count = db.scalar(
        select(func.count(ConnectionProfileFile.id)).where(ConnectionProfileFile.connection_profile_id == profile.id)
    )
    if remaining_count <= 1:
        raise HTTPException(status_code=409, detail="Can't remove the last file on a profile - a file connection profile needs at least one.")

    storage_path = record.storage_path
    profile.schema_metadata_json = None
    profile.last_introspected_at = None
    db.delete(record)
    _audit(db, current_user.id, "connection.file_removed", "connection_profile", profile.id)
    db.commit()
    if os.path.exists(storage_path):
        os.remove(storage_path)
    return None


@router.post("/connection-profiles/{profile_id}/introspect")
def introspect_connection_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # No request body needed anymore - it used to exist purely to carry
    # actor_id, which is now the verified session instead.
    profile = db.scalar(select(ConnectionProfile).where(ConnectionProfile.id == profile_id, ConnectionProfile.owner_id == current_user.id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Connection profile not found")

    try:
        if profile.type == ConnectionType.postgres:
            schema = introspect_schema(
                db_url=postgres_url(profile), schema_name=postgres_schema_name(profile), sample_rows=0,
            )
        elif profile.type == ConnectionType.azure_sql:
            # introspect_schema() itself needs no changes - confirmed
            # it already works via generic SQLAlchemy reflection, not
            # Postgres-specific queries.
            schema = introspect_schema(
                db_url=azure_sql_url(profile), schema_name=azure_sql_schema_name(profile), sample_rows=0,
            )
        elif profile.type == ConnectionType.api:
            creds = api_credentials(profile)
            schema = introspect_api(
                base_url=creds["base_url"], auth_headers=creds["auth_headers"],
                source_name=profile.name, sample_rows=0,
                method=creds["method"], request_body=creds["request_body"],
                pagination=creds["pagination"], oauth2=creds["oauth2"],
            )
        elif profile.type == ConnectionType.file:
            schema = introspect_files(file_credentials(profile), sample_rows=0)
        elif profile.type == ConnectionType.redis:
            schema = introspect_redis(redis_url(profile))
        elif profile.type == ConnectionType.graphql:
            creds = graphql_credentials(profile)
            schema = introspect_graphql(
                endpoint=creds["endpoint"], query=creds["query"], variables=creds["variables"],
                auth_headers=creds["auth_headers"], oauth2=creds["oauth2"],
                source_name=profile.name, sample_rows=0,
            )
        elif profile.type == ConnectionType.soap:
            creds = soap_credentials(profile)
            schema = introspect_soap(
                endpoint=creds["endpoint"], request_body=creds["request_body"],
                soap_version=creds["soap_version"], soap_action=creds["soap_action"],
                auth_headers=creds["auth_headers"], oauth2=creds["oauth2"],
                source_name=profile.name, sample_rows=0,
            )
        else:
            raise HTTPException(status_code=422, detail="Only Postgres, Azure SQL, API, file, Redis, GraphQL, and SOAP connection profiles are supported in v1")
    except CredentialResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=422, detail=f"Could not reach the API: {exc}") from exc
    except redis.exceptions.RedisError as exc:
        raise HTTPException(status_code=422, detail=f"Could not reach Redis: {exc}") from exc

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
    # IntegrityError that would surface as an unhandled 500. Naming the
    # actual blocking project(s), not just "a pipeline exists somewhere",
    # is what makes this actionable - the reviewer needs to know WHERE
    # to go deal with it, not just that they can't proceed here.
    blocking_project_names = list(db.scalars(
        select(Project.name).distinct()
        .join(Pipeline, Pipeline.project_id == Project.id)
        .where((Pipeline.source_connection_id == profile_id) | (Pipeline.destination_connection_id == profile_id))
        .limit(5)
    ))
    if blocking_project_names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This connection profile is used by pipeline(s) in: {', '.join(blocking_project_names)}. "
                "Remove or reassign those pipelines first."
            ),
        )
    _audit(db, current_user.id, "connection.deleted", "connection_profile", profile.id)
    profile_type, profile_id_str = profile.type, str(profile.id)
    db.delete(profile)
    db.commit()
    if profile_type == ConnectionType.file:
        # After commit, not before - if the delete somehow failed to
        # commit, the files should still be there to match the row
        # that's still there. ignore_errors covers the directory
        # already being gone for any reason; a missing directory isn't
        # a failed delete, the goal (no orphaned files) is already met.
        shutil.rmtree(os.path.join(_uploads_base_dir(), profile_id_str), ignore_errors=True)
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


@router.delete("/pipelines/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(pipeline_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Unlike a project or connection profile, a pipeline's own versions,
    files, scaffold files, and runs have no independent existence
    outside it - nothing to "reassign" - so this cascades rather than
    refuses, matching what Pipeline.versions/.runs/.schedule already
    declare in models.py (cascade="all, delete-orphan").

    The one thing that ORM cascade can't handle safely on its own is
    the schedule: deleting the Schedule ROW is automatic once the
    cascade runs, but the LIVE APScheduler job it registered is a
    separate, in-memory thing the database knows nothing about -
    unregister it explicitly first, or the cron keeps firing against a
    pipeline_version_id that no longer resolves to anything at all.
    """
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    if pipeline.schedule is not None:
        try:
            scheduler.remove_job(job_id(pipeline.schedule.id))
        except JobLookupError:
            # Not actually registered right now (e.g. a backend restart
            # since this was last scheduled or edited) - nothing live to
            # clean up, and the DB row is about to be cascade-deleted
            # regardless, so this isn't an error worth blocking on.
            pass
    _audit(db, current_user.id, "pipeline.deleted", "pipeline", pipeline.id)
    db.delete(pipeline)
    db.commit()


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
        proposal = propose_architecture(
            source.schema_metadata_json, goal,
            objectives=pipeline.project.objectives,
            dataset_notes=pipeline.project.dataset_notes,
            known_constraints=pipeline.project.known_constraints,
            load_pattern=pipeline.project.load_pattern.value if pipeline.project.load_pattern else None,
        )
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
            directory=pf.get("directory"),
            purpose=pf["purpose"],
            reads_from=pf.get("reads_from", []),
            destination_table=pf["destination_table"],
        ))

    # Same moment, same reasoning: the approved plan's scaffold entries
    # are materialized as real rows here too. Only generated=true
    # entries get one - "readme"/"license" are planning-only and never
    # produce a row (see ScaffoldFileType's own docstring in models.py).
    proposed_scaffold = (version.architecture_proposal or {}).get("project_structure") or []
    scaffold_count = 0
    for sf in proposed_scaffold:
        if not sf.get("generated"):
            continue
        db.add(PipelineVersionScaffoldFile(
            pipeline_version_id=version.id,
            path=sf["path"],
            purpose=sf["purpose"],
            scaffold_type=sf["scaffold_type"],
        ))
        scaffold_count += 1

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
    return {"version_id": version.id, "architecture_status": "approved", "file_count": len(proposed_files),
            "scaffold_file_count": scaffold_count}


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


@router.post("/pipelines/{pipeline_id}/generate", status_code=status.HTTP_202_ACCEPTED)
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

    from generation_tasks import run_pipeline_generation
    version_number = pipeline.version + 1
    version = PipelineVersion(
        pipeline_id=pipeline.id, version=version_number,
        generated_code="# Generation in progress...\n",
        created_by=current_user.id, review_status=PipelineVersionReviewStatus.testing,
        architecture_proposal=current_version.architecture_proposal,
        architecture_status=current_version.architecture_status,
        generation_in_progress=True,
    )
    pipeline.version = version_number
    pipeline.status = PipelineStatus.testing
    db.add(version)
    db.flush()
    db.commit()

    run_pipeline_generation.delay(str(pipeline.id), str(version.id), goal, body.max_retries, str(current_user.id))
    return {"pipeline_id": pipeline.id, "version_id": version.id, "review_status": version.review_status}


@router.post("/pipeline-version-files/{file_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_and_test_file(file_id: uuid.UUID, body: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Validates the request and dispatches generation to a Celery task
    (generation_tasks.run_file_generation) - mirrors
    generate_and_test_pipeline's dispatch pattern exactly. Every
    validation/precondition check below is unchanged from before this
    became async; what moved is the AI call, sandbox execution, and
    everything that depends on them, all now re-derived fresh inside
    the task itself rather than computed here and passed through the
    queue (see generation_tasks.py's module docstring for why: no
    decrypted credential ever belongs in a queue message).
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
    # The gate this entire pinned-schema feature depends on for its
    # actual guarantee: without this check, nothing would stop code
    # generation from running before (or instead of) the schema ever
    # being approved and applied - the whole point is that the table's
    # real structure is fixed BEFORE any transformation code exists,
    # not decided alongside it. Checking BOTH schema_review_status and
    # schema_applied_at, not just the status alone, even though
    # approve_file_schema always sets both together atomically today -
    # a cheap extra layer of "verify, don't assume" rather than trust
    # that invariant holds forever as this code evolves.
    if version.uses_pinned_schema and (
        file.schema_review_status != PipelineVersionReviewStatus.approved or file.schema_applied_at is None
    ):
        raise HTTPException(status_code=409, detail="This file's schema must be proposed and approved before its code can be generated")
    if file.review_status not in (PipelineVersionReviewStatus.draft, PipelineVersionReviewStatus.testing, PipelineVersionReviewStatus.rejected):
        raise HTTPException(status_code=409, detail="This file has already been generated and is awaiting review, or is finalized")

    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        raise HTTPException(status_code=409, detail="Pipeline connection profile is missing")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before generating a pipeline")

    pipeline.status = PipelineStatus.testing
    file.review_status = PipelineVersionReviewStatus.testing
    file.generation_in_progress = True
    db.commit()

    from generation_tasks import run_file_generation
    run_file_generation.delay(str(file.id), body.max_retries, str(current_user.id))
    return {"file_id": file.id, "review_status": file.review_status}


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


@router.post("/pipeline-version-files/{file_id}/propose-schema")
def propose_file_schema_endpoint(file_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Proposes this file's destination schema as its own reviewable
    artifact - BEFORE any transformation code exists for it. Only
    applies to a version that opted into uses_pinned_schema; this
    endpoint is a 404-adjacent 409 for anything else, not a silent
    no-op.

    Synchronous, not dispatched to a Celery task - this is a pure AI
    call with no sandbox execution at all (propose_file_schema.py never
    touches Docker), matching the exact same convention already
    established for propose_pipeline_architecture and
    generate_scaffold_file_endpoint, the other two AI-only endpoints
    that were deliberately left synchronous when sandbox-heavy
    generation moved to Celery.
    """
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    version = _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)
    pipeline = _owned_pipeline(db, version.pipeline_id, current_user.id, lock=True)

    if not version.uses_pinned_schema:
        raise HTTPException(status_code=409, detail="This pipeline version did not opt into pinned schemas")
    if file.schema_review_status not in (None, PipelineVersionReviewStatus.rejected):
        raise HTTPException(status_code=409, detail="This file's schema has already been proposed and is awaiting review, or is finalized")
    # Same ordering principle as generate_and_test_file: an earlier
    # file's review_status only reaches "approved" once BOTH its schema
    # and its code have been approved (generate_and_test_file itself
    # requires schema_review_status == approved before code generation
    # can even start) - so reusing this exact check is sufficient on
    # its own; no separate schema-specific ordering check is needed.
    earlier_unapproved = db.scalar(
        select(PipelineVersionFile.id).where(
            PipelineVersionFile.pipeline_version_id == version.id,
            PipelineVersionFile.file_order < file.file_order,
            PipelineVersionFile.review_status != PipelineVersionReviewStatus.approved,
        ).limit(1)
    )
    if earlier_unapproved is not None:
        raise HTTPException(status_code=409, detail="An earlier file in this build plan hasn't been fully approved yet")

    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        raise HTTPException(status_code=409, detail="Pipeline connection profile is missing")
    if destination.type != ConnectionType.postgres:
        raise HTTPException(status_code=409, detail="Pinned schemas require a Postgres destination - CREATE TABLE has no meaning for a non-SQL destination")
    if not source.schema_metadata_json:
        raise HTTPException(status_code=409, detail="Introspect the source connection before proposing a schema")

    # Earlier files' own already-approved DDL text, for real foreign-key
    # context - not a fresh introspection round-trip against the real
    # destination, since the DDL text this project already generated
    # and applied unambiguously encodes the same real structure.
    earlier_files = db.scalars(
        select(PipelineVersionFile).where(
            PipelineVersionFile.pipeline_version_id == version.id,
            PipelineVersionFile.file_order < file.file_order,
        ).order_by(PipelineVersionFile.file_order)
    ).all()
    earlier_file_schemas = {earlier.destination_table: earlier.schema_ddl for earlier in earlier_files if earlier.schema_ddl}

    from propose_file_schema import propose_file_schema
    try:
        result = propose_file_schema(
            source.schema_metadata_json, file.purpose, file.reads_from, file.destination_table,
            earlier_file_schemas=earlier_file_schemas,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Schema proposal failed: {exc}") from exc

    file.schema_ddl = result["ddl"]
    file.schema_review_status = PipelineVersionReviewStatus.pending_review
    file.schema_rejection_comment = None
    _audit(db, current_user.id, "pipeline_version_file.schema_proposed", "pipeline_version_file", file.id)
    db.commit()
    return {"file_id": file.id, "schema_ddl": file.schema_ddl, "schema_review_status": file.schema_review_status}


@router.post("/pipeline-version-files/{file_id}/approve-schema")
def approve_file_schema(file_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Approving a proposed schema isn't just a status flip - it's the
    point where CREATE TABLE (and any CREATE INDEX statements) actually
    run against the real destination, for real. The table genuinely
    starts existing with exactly this structure right here; nothing
    else in this feature creates it.

    Executes the rendered DDL as separate, individually-executed
    statements within one transaction, rather than sending the whole
    multi-statement text through a single engine.execute(text(...))
    call - a real, unverified uncertainty about whether that executes
    cleanly across SQLAlchemy/psycopg2 versions and dialects that this
    sidesteps entirely rather than gambles on, the same "don't bet on
    an unverified assumption when a guaranteed-safe alternative exists"
    principle behind several other choices already made this session.
    render_ddl() always separates statements with ";\\n", so splitting
    on that exactly reverses how it was built.

    On any failure - a syntax problem, a real conflict with something
    already in the database - nothing is marked approved and
    schema_applied_at stays null. A partially-applied DDL (e.g. CREATE
    TABLE succeeded but one CREATE INDEX statement failed) rolls back
    entirely via the transaction, never left half-applied.
    """
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    version = _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)
    pipeline = _owned_pipeline(db, version.pipeline_id, current_user.id, lock=True)

    if file.schema_review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a schema awaiting review can be approved")

    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if destination is None:
        raise HTTPException(status_code=409, detail="Pipeline destination connection profile is missing")

    destination_url = postgres_url(destination)
    statements = [s.strip() for s in file.schema_ddl.split(";\n") if s.strip()]
    engine = sa.create_engine(destination_url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(sa.text(statement.rstrip(";") + ";"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Applying this schema to the real destination failed, nothing was approved: {exc}") from exc
    finally:
        engine.dispose()

    file.schema_review_status = PipelineVersionReviewStatus.approved
    file.schema_applied_at = datetime.now(timezone.utc)
    file.schema_rejection_comment = None
    _audit(db, current_user.id, "pipeline_version_file.schema_approved", "pipeline_version_file", file.id)
    db.commit()
    return {"file_id": file.id, "schema_review_status": "approved", "schema_applied_at": file.schema_applied_at}


@router.post("/pipeline-version-files/{file_id}/reject-schema")
def reject_file_schema(file_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Rejecting a schema never runs any DDL - nothing about the real
    destination changes. body.comment is stored directly on the file
    (schema_rejection_comment) rather than through PipelineReview,
    since that table has no way to distinguish a schema comment from a
    code comment on the same file - see the model's own comment on
    schema_rejection_comment for the full reasoning.
    """
    file = db.scalar(select(PipelineVersionFile).where(PipelineVersionFile.id == file_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    _owned_version(db, file.pipeline_version_id, current_user.id, lock=True)

    if file.schema_review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a schema awaiting review can be rejected")

    file.schema_review_status = PipelineVersionReviewStatus.rejected
    file.schema_rejection_comment = body.comment
    _audit(db, current_user.id, "pipeline_version_file.schema_rejected", "pipeline_version_file", file.id)
    db.commit()
    return {"file_id": file.id, "schema_review_status": "rejected"}


@router.post("/pipeline-version-scaffold-files/{scaffold_file_id}/generate")
def generate_scaffold_file_endpoint(scaffold_file_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate real content for one approved scaffold-file plan entry.

    No sandbox test the way pipeline files get one - a docker-compose.yml
    or requirements.txt has no comparable "run" step. Generation is
    followed by a light, type-appropriate validation pass instead
    (_validate_scaffold_content) - genuinely broken output (invalid
    YAML, invalid Python) routes back to 'testing' for a retry, the
    same review_status pipeline files use for a failed sandbox run.
    """
    scaffold_file = db.scalar(
        select(PipelineVersionScaffoldFile).where(PipelineVersionScaffoldFile.id == scaffold_file_id).with_for_update()
    )
    if scaffold_file is None:
        raise HTTPException(status_code=404, detail="Scaffold file not found")
    version = _owned_version(db, scaffold_file.pipeline_version_id, current_user.id, lock=True)
    if scaffold_file.review_status not in (PipelineVersionReviewStatus.draft, PipelineVersionReviewStatus.testing, PipelineVersionReviewStatus.rejected):
        raise HTTPException(status_code=409, detail="This scaffold file has already been generated and is awaiting review, or is finalized")

    # The approved build plan's real files give the AI concrete context
    # (what actually gets imported, what actually needs orchestrating) -
    # same reasoning as generate_and_test_file's file_goal construction.
    build_plan_files = [
        {"file_name": f.file_name, "purpose": f.purpose, "reads_from": f.reads_from, "destination_table": f.destination_table}
        for f in db.scalars(
            select(PipelineVersionFile).where(PipelineVersionFile.pipeline_version_id == version.id)
            .order_by(PipelineVersionFile.file_order)
        )
    ]

    from generate_scaffold_file import generate_scaffold_file
    try:
        generated = generate_scaffold_file(
            scaffold_type=scaffold_file.scaffold_type.value,
            path=scaffold_file.path,
            purpose=scaffold_file.purpose,
            build_plan_files=build_plan_files,
        )
    except Exception as exc:
        scaffold_file.review_status = PipelineVersionReviewStatus.testing
        db.commit()
        raise HTTPException(status_code=502, detail=f"Scaffold generation failed: {exc}") from exc

    content = generated.get("content", "")
    validation_error = _validate_scaffold_content(scaffold_file.scaffold_type.value, content)

    scaffold_file.generated_content = content
    scaffold_file.review_status = PipelineVersionReviewStatus.testing if validation_error else PipelineVersionReviewStatus.pending_review
    _audit(db, current_user.id,
           "pipeline_version_scaffold_file.validation_failed" if validation_error else "pipeline_version_scaffold_file.ready_for_review",
           "pipeline_version_scaffold_file", scaffold_file.id)
    db.commit()
    return {"scaffold_file_id": scaffold_file.id, "review_status": scaffold_file.review_status,
            "validation_error": validation_error}


@router.post("/pipeline-version-scaffold-files/{scaffold_file_id}/approve")
def approve_scaffold_file(scaffold_file_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    scaffold_file = db.scalar(
        select(PipelineVersionScaffoldFile).where(PipelineVersionScaffoldFile.id == scaffold_file_id).with_for_update()
    )
    if scaffold_file is None:
        raise HTTPException(status_code=404, detail="Scaffold file not found")
    version = _owned_version(db, scaffold_file.pipeline_version_id, current_user.id, lock=True)
    if scaffold_file.review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a scaffold file awaiting review can be approved")

    scaffold_file.review_status = PipelineVersionReviewStatus.approved
    scaffold_file.reviewed_by = current_user.id
    scaffold_file.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, pipeline_version_scaffold_file_id=scaffold_file.id,
                          actor_id=current_user.id, action=PipelineReviewAction.approved, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version_scaffold_file.approved", "pipeline_version_scaffold_file", scaffold_file.id)
    db.commit()
    return {"scaffold_file_id": scaffold_file.id, "review_status": "approved"}


@router.post("/pipeline-version-scaffold-files/{scaffold_file_id}/reject")
def reject_scaffold_file(scaffold_file_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    scaffold_file = db.scalar(
        select(PipelineVersionScaffoldFile).where(PipelineVersionScaffoldFile.id == scaffold_file_id).with_for_update()
    )
    if scaffold_file is None:
        raise HTTPException(status_code=404, detail="Scaffold file not found")
    version = _owned_version(db, scaffold_file.pipeline_version_id, current_user.id, lock=True)
    if scaffold_file.review_status != PipelineVersionReviewStatus.pending_review:
        raise HTTPException(status_code=409, detail="Only a scaffold file awaiting review can be rejected")
    scaffold_file.review_status = PipelineVersionReviewStatus.rejected
    scaffold_file.reviewed_by = current_user.id
    scaffold_file.reviewed_at = datetime.now(timezone.utc)
    db.add(PipelineReview(pipeline_version_id=version.id, pipeline_version_scaffold_file_id=scaffold_file.id,
                          actor_id=current_user.id, action=PipelineReviewAction.rejected, comment=body.comment))
    _audit(db, current_user.id, "pipeline_version_scaffold_file.rejected", "pipeline_version_scaffold_file", scaffold_file.id)
    db.commit()
    return {"scaffold_file_id": scaffold_file.id, "review_status": "rejected"}


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
            "generation_in_progress": f.generation_in_progress,
            "schema_ddl": f.schema_ddl, "schema_review_status": f.schema_review_status,
            "schema_applied_at": f.schema_applied_at, "schema_rejection_comment": f.schema_rejection_comment,
            "runs": [{"id": r.id, "status": r.status, "started_at": r.started_at,
                      "finished_at": r.finished_at, "log_output": r.log_output,
                      "error_output": r.error_output, "row_count": r.row_count,
                      "quality_checks": r.quality_checks} for r in file_runs],
        })

    # Same shape as files_payload above, for the version's scaffold
    # files - no runs list, since scaffold files have no sandbox
    # evidence the way pipeline files do.
    scaffold_files = list(db.scalars(
        select(PipelineVersionScaffoldFile).where(PipelineVersionScaffoldFile.pipeline_version_id == current.id)
        .order_by(PipelineVersionScaffoldFile.path)
    ))
    scaffold_files_payload = [
        {
            "id": sf.id, "path": sf.path, "purpose": sf.purpose,
            "scaffold_type": sf.scaffold_type, "generated_content": sf.generated_content,
            "review_status": sf.review_status,
        }
        for sf in scaffold_files
    ]

    # None when the pipeline has never been scheduled - the frontend
    # needs to tell "no schedule yet" apart from "a schedule exists",
    # since editing an existing schedule should start from its real
    # current cron_expression, not a hardcoded default.
    schedule = db.scalar(select(Schedule).where(Schedule.pipeline_id == pipeline.id))
    schedule_payload = (
        {"id": schedule.id, "cron_expression": schedule.cron_expression, "next_run_at": schedule.next_run_at}
        if schedule is not None else None
    )

    return {
        "pipeline": {"id": pipeline.id, "status": pipeline.status, "current_version": pipeline.version},
        "version": {
            "id": current.id, "number": current.version, "code": current.generated_code,
            "review_status": current.review_status, "reviewed_by": current.reviewed_by,
            "reviewed_at": current.reviewed_at,
            "architecture_proposal": current.architecture_proposal,
            "architecture_status": current.architecture_status,
            "generation_in_progress": current.generation_in_progress,
            "uses_pinned_schema": current.uses_pinned_schema,
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
        "scaffold_files": scaffold_files_payload,
        "schedule": schedule_payload,
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


@router.delete("/pipelines/{pipeline_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def unschedule_pipeline(pipeline_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Stops the pipeline from running on its cron - the standalone
    counterpart to delete_pipeline's own schedule-teardown, extracted
    for exactly the gap that endpoint's own docstring left open: there
    was no way to just stop a pipeline from running while keeping the
    pipeline itself, its versions, and its review history intact.
    delete_pipeline still does the same live-job teardown as part of
    removing everything; this does only that part, and nothing else.

    pipeline.status reverts to approved - the only state
    schedule_approved_pipeline's own precondition allows a pipeline to
    have been scheduled FROM in the first place, so it's the only
    correct state to revert TO.
    """
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id, lock=True)
    schedule = db.scalar(select(Schedule).where(Schedule.pipeline_id == pipeline.id).with_for_update())
    if schedule is None:
        raise HTTPException(status_code=409, detail="This pipeline has no active schedule to remove")
    try:
        scheduler.remove_job(job_id(schedule.id))
    except JobLookupError:
        # Not actually registered right now (e.g. a backend restart
        # since this was last scheduled) - nothing live to clean up,
        # and the DB row is about to be deleted regardless.
        pass
    pipeline.status = PipelineStatus.approved
    db.delete(schedule)
    _audit(db, current_user.id, "pipeline.unscheduled", "pipeline", pipeline.id)
    db.commit()
    return None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "pipeline"


@router.get("/pipelines/{pipeline_id}/export")
def export_pipeline(pipeline_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Download the current version's real code - a single .py only
    when there's truly nothing else to include (one pipeline file, no
    approved scaffold files); a .zip (respecting each file's directory/
    path placement) otherwise. Gated on the version genuinely being
    approved, same principle as scheduling: nothing half-reviewed
    leaves the system - scaffold files are the one exception, since
    their own approval was always designed as supplementary rather
    than gating (see PipelineVersionScaffoldFile's docstring): whichever
    of them happen to be approved at export time are included, but an
    unapproved or nonexistent scaffold file never blocks exporting the
    pipeline itself.
    """
    pipeline = _owned_pipeline(db, pipeline_id, current_user.id)
    version = db.scalar(select(PipelineVersion).where(
        PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version == pipeline.version
    ))
    if version is None or version.review_status != PipelineVersionReviewStatus.approved:
        raise HTTPException(status_code=409, detail="Only an approved pipeline version can be exported")

    project_slug = _slugify(pipeline.project.name)
    files = list(db.scalars(
        select(PipelineVersionFile).where(PipelineVersionFile.pipeline_version_id == version.id)
        .order_by(PipelineVersionFile.file_order)
    ))
    approved_scaffold_files = list(db.scalars(
        select(PipelineVersionScaffoldFile).where(
            PipelineVersionScaffoldFile.pipeline_version_id == version.id,
            PipelineVersionScaffoldFile.review_status == PipelineVersionReviewStatus.approved,
        ).order_by(PipelineVersionScaffoldFile.path)
    ))

    _audit(db, current_user.id, "pipeline.exported", "pipeline", pipeline.id)
    db.commit()

    if files or approved_scaffold_files:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                archive_path = f"{f.directory}/{f.file_name}" if f.directory else f.file_name
                zf.writestr(archive_path, f.generated_code or "")
            for sf in approved_scaffold_files:
                zf.writestr(sf.path, sf.generated_content or "")
            # A non-multi-file version's code lives on version.generated_code
            # directly, not in any PipelineVersionFile row - if scaffold
            # files are the only reason this export is a zip at all (files
            # is empty), that code still needs to be IN the zip somewhere,
            # or it would be silently missing entirely.
            if not files:
                zf.writestr(f"{project_slug}_pipeline.py", version.generated_code)
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{project_slug}_pipeline.zip"'},
        )

    return Response(
        content=version.generated_code,
        media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{project_slug}_pipeline.py"'},
    )

