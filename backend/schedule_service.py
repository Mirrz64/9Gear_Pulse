"""APScheduler integration that executes only the approved, pinned revision."""
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from connection_service import CredentialResolutionError, postgres_url
from heal_pipeline import run_in_sandbox
from models import ConnectionProfile, PipelineRun, PipelineVersion, RunStatus, Schedule
from session import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def job_id(schedule_id: uuid.UUID) -> str:
    return f"approved-pipeline-{schedule_id}"


def run_pinned_version(pipeline_version_id: uuid.UUID) -> None:
    """Never regenerate or read the pipeline's mutable latest-code column.

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
    with SessionLocal() as db:
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
            source_url = postgres_url(source)
            dest_url = postgres_url(destination)
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
                    success, logs = run_in_sandbox(script_path, source_url, dest_url)
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
            success, logs = run_in_sandbox(script_path, source_url, dest_url)
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
