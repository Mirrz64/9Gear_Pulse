"""APScheduler integration that executes only the approved, pinned revision."""
import hashlib
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from connection_service import CredentialResolutionError
from generation_tasks import _resolve_destination_execution_params, _resolve_source_execution_params
from heal_pipeline import run_in_sandbox
from models import ConnectionProfile, PipelineRun, PipelineVersion, RunStatus, Schedule
from session import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def job_id(schedule_id: uuid.UUID) -> str:
    return f"approved-pipeline-{schedule_id}"


def _advisory_lock_key(value: uuid.UUID) -> int:
    """Deterministic Postgres bigint from a UUID, for pg_try_advisory_lock.

    Python's built-in hash() is randomized per-process (PYTHONHASHSEED)
    and would produce a DIFFERENT key on each backend replica for the
    identical logical job - defeating the entire point of a lock meant
    to coordinate ACROSS replicas. A stable hash (SHA-256, truncated to
    8 bytes and read as a signed 64-bit int, matching Postgres's signed
    bigint range) gives every replica the exact same key for the exact
    same UUID, always.
    """
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def run_pinned_version(pipeline_version_id: uuid.UUID) -> None:
    """Entry point APScheduler actually calls. Running more than one
    backend replica means each replica has its OWN independent
    in-memory scheduler, and every one of them will decide the same
    cron-triggered job is due at the same moment - a shared jobstore
    only shares job DEFINITIONS, it does nothing to stop multiple
    replicas from each executing the same firing. A non-blocking
    Postgres advisory lock, scoped to this pipeline_version_id, ensures
    only ONE replica's invocation actually runs the pipeline; every
    other replica sees the lock already held and returns immediately -
    no duplicate PipelineRun rows, no duplicate writes to a real
    destination database from the same scheduled fire.

    Session-level locks (pg_try_advisory_lock/pg_advisory_unlock), not
    the _xact transaction-scoped variants, are used deliberately: the
    actual work below commits multiple times as it goes (one commit per
    file in a multi-file run) and the lock must survive all of them,
    not release at the first commit. The same db session/connection is
    used for the lock and for all of that work, since a session-level
    advisory lock is tied to the specific connection that acquired it.
    """
    with SessionLocal() as db:
        lock_key = _advisory_lock_key(pipeline_version_id)
        acquired = db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
        if not acquired:
            logger.info(
                "Scheduled run for pipeline_version %s skipped - another replica already holds the lock",
                pipeline_version_id,
            )
            return
        try:
            _run_pinned_version_locked(db, pipeline_version_id)
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            db.commit()


def _run_pinned_version_locked(db: Session, pipeline_version_id: uuid.UUID) -> None:
    """The actual scheduled-run logic - unchanged from before the
    advisory lock existed, just moved into its own function so
    run_pinned_version above can wrap it in acquire/release without
    needing to touch any of its internal control flow (its several
    early `return`s all still correctly trigger the caller's `finally`
    release, exactly as they did when this was one function).

    Never regenerate or read the pipeline's mutable latest-code column.

    Every exit path that has a pipeline to attach a result to records a
    PipelineRun, success or failure. This runs as an APScheduler
    background job, not inside an HTTP request - an exception here
    doesn't turn into an HTTP response, it just gets swallowed by
    APScheduler's own error handling. A scheduled run failing silently
    (no row, no audit trail) is worse than it failing loudly, since
    "did this actually run" is exactly what the dashboard needs to be
    able to answer.

    Multi-file versions (Stage B) run each file's own pinned code in
    file_order, stopping at the first failure - a later file depends on
    an earlier one's destination table genuinely being up to date, so
    running it anyway after an earlier failure would mean silently
    processing stale or incomplete data instead of correctly halting.
    Single-file versions (version.files empty) run exactly as before,
    completely unchanged.
    """
    version = db.get(PipelineVersion, pipeline_version_id)
    if version is None:
        # Nothing to attach a run to - a schedule pointing at a
        # deleted version is a data-integrity issue elsewhere, not
        # a normal run failure. Logged so it's not entirely silent.
        logger.error("Scheduled run skipped: pipeline_version %s no longer exists", pipeline_version_id)
        return

    pipeline = version.pipeline
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)

    if source is None or destination is None:
        db.add(PipelineRun(
            pipeline_id=pipeline.id, pipeline_version_id=version.id,
            status=RunStatus.failed,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            error_output="Scheduled run failed: source or destination connection profile no longer exists.",
        ))
        db.commit()
        return

    try:
        source_url, source_extra_env, needs_source_db, source_files_dir = _resolve_source_execution_params(source)
        dest_url, dest_extra_env, needs_dest_db = _resolve_destination_execution_params(destination)
        extra_env = {**(source_extra_env or {}), **dest_extra_env}
        # run_in_sandbox's real signature has been confirmed directly
        # against heal_pipeline.py - it accepts extra_env/needs_source_db/
        # source_files_dir/needs_dest_db exactly as passed below, and
        # execute_with_self_healing forwards them to it unchanged on
        # each retry attempt. needs_dest_db specifically matters beyond
        # just correctness: without it, a Redis-destination scheduled
        # run would have _resolve_sandbox_db_urls() silently fall back
        # to this app's own control-plane database as DEST_DB_URL - a
        # real credential exposure heal_pipeline.py's own needs_dest_db
        # parameter was added specifically to close.
    except CredentialResolutionError as exc:
        db.add(PipelineRun(
            pipeline_id=pipeline.id, pipeline_version_id=version.id,
            status=RunStatus.failed,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            error_output=f"Scheduled run failed: could not resolve connection credentials - {exc}",
        ))
        db.commit()
        return

    if version.files:
        # Multi-file (Stage B) path. version.files is already
        # ordered by file_order via the relationship definition.
        for file in version.files:
            if not file.generated_code:
                # Shouldn't happen for a version that made it through
                # scheduling (every file must be approved, which
                # requires real generated code, before the whole
                # version can be marked ready) - but a schedule
                # pointing at a version whose files aren't actually
                # all in that state is a data-integrity problem, same
                # class as "version no longer exists" above. Stop and
                # record it rather than crash or silently skip.
                db.add(PipelineRun(
                    pipeline_id=pipeline.id, pipeline_version_id=version.id,
                    pipeline_version_file_id=file.id,
                    status=RunStatus.failed,
                    started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                    error_output=f"Scheduled run failed: file '{file.file_name}' has no generated code.",
                ))
                db.commit()
                return

            file_started_at = datetime.now(timezone.utc)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
                script.write(file.generated_code)
                script_path = script.name
            try:
                success, logs = run_in_sandbox(
                    script_path, source_url, dest_url,
                    extra_env=extra_env, needs_source_db=needs_source_db, source_files_dir=source_files_dir,
                    needs_dest_db=needs_dest_db,
                )
            except Exception as exc:
                success = False
                logs = f"Unexpected error during scheduled sandbox run of '{file.file_name}': {exc}"
            finally:
                os.unlink(script_path)

            db.add(PipelineRun(
                pipeline_id=pipeline.id, pipeline_version_id=version.id,
                pipeline_version_file_id=file.id,
                status=RunStatus.success if success else RunStatus.failed,
                started_at=file_started_at, finished_at=datetime.now(timezone.utc),
                log_output=logs if success else None, error_output=None if success else logs,
            ))
            db.commit()

            if not success:
                logger.error(
                    "Scheduled run stopped: file '%s' (pipeline_version %s) failed, "
                    "later files depend on its output and were not run",
                    file.file_name, version.id,
                )
                return
        return

    # Single-file path - unchanged from before Stage B existed.
    started_at = datetime.now(timezone.utc)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
        script.write(version.generated_code)
        script_path = script.name
    try:
        success, logs = run_in_sandbox(
            script_path, source_url, dest_url,
            extra_env=extra_env, needs_source_db=needs_source_db, source_files_dir=source_files_dir,
            needs_dest_db=needs_dest_db,
        )
    except Exception as exc:
        # run_in_sandbox already catches its own internal exceptions
        # and returns (False, message) rather than raising - this is
        # a last-resort net for anything that still slips through,
        # so even a genuinely unexpected failure gets recorded
        # instead of crashing the scheduler thread silently.
        success = False
        logs = f"Unexpected error during scheduled sandbox run: {exc}"
    finally:
        os.unlink(script_path)

    db.add(PipelineRun(
        pipeline_id=pipeline.id, pipeline_version_id=version.id,
        status=RunStatus.success if success else RunStatus.failed,
        started_at=started_at, finished_at=datetime.now(timezone.utc),
        log_output=logs if success else None, error_output=None if success else logs,
    ))
    db.commit()


def register_schedule(db: Session, schedule: Schedule) -> None:
    """Registers the job with APScheduler and writes the computed next
    run time back onto the Schedule row. Previously this only lived in
    APScheduler's own internal jobstore, so schedules.next_run_at (a
    real column in the schema) stayed permanently null - nothing
    reading straight from Postgres could ever show "next run at X"
    without separately querying APScheduler's live state.
    """
    job = scheduler.add_job(
        run_pinned_version, CronTrigger.from_crontab(schedule.cron_expression),
        id=job_id(schedule.id), replace_existing=True,
        kwargs={"pipeline_version_id": schedule.pipeline_version_id},
    )
    schedule.next_run_at = job.next_run_time
    db.commit()


def restore_schedules() -> None:
    with SessionLocal() as db:
        for schedule in db.scalars(select(Schedule).where(Schedule.pipeline_version_id.is_not(None))):
            register_schedule(db, schedule)
