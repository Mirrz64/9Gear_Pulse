"""Celery tasks for AI generation + sandbox execution, moved out of the
synchronous HTTP request path in review_api.py.

Deliberate design choice: tasks take only IDs and small primitives
(max_retries) as arguments, never decrypted credentials, schema
dictionaries, or prompt text pre-computed by the endpoint. A Celery
task argument is a message sitting in Redis - passing a decrypted
database connection string (a real secret) through it would mean a
plaintext credential briefly at rest in the broker, an unnecessary
exposure entirely avoidable by having the task re-derive it itself,
which it can just as cheaply since it opens its own database session
anyway. This also means each task independently re-validates that the
row it's operating on still exists by the time it actually runs, not
just at the moment the endpoint dispatched it - a worker queue can be
backed up, and re-deriving state is what makes the gap between
"queued" and "started" safe rather than assumed-instant.

The endpoint retains every validation/precondition check exactly as
before (ownership, locks, ordering, architecture-approval, connection
profiles existing) - a genuinely malformed or disallowed request still
gets an immediate, synchronous 409/404, never a task silently queued
only to fail later. Only once all of that passes does the endpoint
flip generation_in_progress and dispatch; from there, these tasks do
the same work the endpoint used to do inline, using their own fresh
session throughout.
"""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

# Guarantees this file's own directory (backend/) is importable,
# regardless of how or from where the Celery worker process itself was
# launched. review_api.py's identical deferred imports (generate_pipeline,
# heal_pipeline, data_quality) work without this because uvicorn's own
# process-start path resolution already covers them - but a task's
# lazily-executed import, deep inside a function body running under a
# worker, doesn't reliably see the same sys.path the celery CLI set up
# for loading celery_app itself at startup. Rather than depend on
# getting that exact mechanism right, this makes the guarantee
# unconditional: sibling modules in this same directory (generate_pipeline,
# heal_pipeline, data_quality) are always importable from here, no
# matter what pool type or invocation path was used to start the worker.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import bindparam, select
import sqlalchemy as sa

from audit import _audit
from celery_app import celery_app
from connection_service import CredentialResolutionError, api_credentials, file_credentials, graphql_credentials, postgres_url, redis_url, soap_credentials
from introspect import introspect_schema
from models import (
    ConnectionType,
    Pipeline,
    PipelineReview,
    PipelineReviewAction,
    PipelineRun,
    PipelineStatus,
    PipelineVersion,
    PipelineVersionFile,
    PipelineVersionReviewStatus,
    RunStatus,
)
from session import SessionLocal


def _split_destination_table(dataset: str, destination_table: str) -> tuple[str, str]:
    """Duplicated from review_api.py rather than imported - review_api.py
    dispatches these tasks (imports FROM this module), so importing
    back from review_api.py here would be a circular import. Same
    trivial logic either way: destination_table is already
    schema-qualified ("schema.table"), so a redundant dataset prefix
    must be stripped before calling run_quality_checks(), which expects
    the two as separate bare strings.
    """
    if "." in destination_table:
        schema, table = destination_table.split(".", 1)
        return schema, table
    return dataset, destination_table


def _resolve_source_execution_params(source):
    """The sole, authoritative version of this dispatch logic - review_api.py
    used to have its own identical copy (kept in sync for the same
    circular-import reason _split_destination_table above still is), but
    that copy was dead code, unused since generation moved entirely into
    these Celery tasks, and has been removed rather than left around
    looking active. This is the only place this logic lives now.
    """
    if source.type == ConnectionType.api:
        creds = api_credentials(source)
        env = {
            "SOURCE_API_BASE_URL": creds["base_url"],
            "SOURCE_API_AUTH_HEADERS": json.dumps(creds["auth_headers"]),
            "SOURCE_API_METHOD": creds["method"],
            "SOURCE_API_BODY": json.dumps(creds["request_body"]),
        }
        if creds["pagination"]:
            env["SOURCE_API_PAGINATION"] = json.dumps(creds["pagination"])
        if creds["oauth2"]:
            # Raw config, never a pre-fetched token - see
            # api_credentials()'s own docstring in connection_service.py
            # for why generated code must fetch (and refresh) its own
            # token at actual execution time rather than receive one
            # baked in here, which could easily be expired by the time
            # a self-healing retry actually runs.
            oauth2 = creds["oauth2"]
            env["SOURCE_API_OAUTH_TOKEN_URL"] = oauth2["token_url"]
            env["SOURCE_API_OAUTH_CLIENT_ID"] = oauth2["client_id"]
            env["SOURCE_API_OAUTH_CLIENT_SECRET"] = oauth2["client_secret"]
            if oauth2.get("scope"):
                env["SOURCE_API_OAUTH_SCOPE"] = oauth2["scope"]
        return None, env, False, None
    if source.type == ConnectionType.redis:
        return None, {"SOURCE_REDIS_URL": redis_url(source)}, False, None
    if source.type == ConnectionType.graphql:
        creds = graphql_credentials(source)
        env = {
            "SOURCE_GRAPHQL_ENDPOINT": creds["endpoint"],
            "SOURCE_GRAPHQL_QUERY": creds["query"],
            "SOURCE_GRAPHQL_VARIABLES": json.dumps(creds["variables"]),
            # Deliberately the SAME env var names the API source type
            # already uses (SOURCE_API_AUTH_HEADERS / SOURCE_API_OAUTH_*),
            # not GraphQL-specific ones - generated code reuses the
            # exact auth-fetch-and-refresh pattern the AI already knows
            # from the API guidance, rather than needing a second,
            # parallel auth mechanism taught from scratch for a
            # genuinely identical concern (GraphQL auth is typically
            # indistinguishable from REST API auth in practice).
            "SOURCE_API_AUTH_HEADERS": json.dumps(creds["auth_headers"]),
        }
        if creds["oauth2"]:
            oauth2 = creds["oauth2"]
            env["SOURCE_API_OAUTH_TOKEN_URL"] = oauth2["token_url"]
            env["SOURCE_API_OAUTH_CLIENT_ID"] = oauth2["client_id"]
            env["SOURCE_API_OAUTH_CLIENT_SECRET"] = oauth2["client_secret"]
            if oauth2.get("scope"):
                env["SOURCE_API_OAUTH_SCOPE"] = oauth2["scope"]
        return None, env, False, None
    if source.type == ConnectionType.soap:
        creds = soap_credentials(source)
        env = {
            "SOURCE_SOAP_ENDPOINT": creds["endpoint"],
            "SOURCE_SOAP_VERSION": creds["soap_version"],
            "SOURCE_SOAP_ACTION": creds["soap_action"],
            "SOURCE_SOAP_BODY": creds["request_body"],
            # Same reasoning as GraphQL just above - reuse the API
            # source's own auth env var names rather than invent
            # SOAP-specific ones for a genuinely identical mechanism.
            "SOURCE_API_AUTH_HEADERS": json.dumps(creds["auth_headers"]),
        }
        if creds["oauth2"]:
            oauth2 = creds["oauth2"]
            env["SOURCE_API_OAUTH_TOKEN_URL"] = oauth2["token_url"]
            env["SOURCE_API_OAUTH_CLIENT_ID"] = oauth2["client_id"]
            env["SOURCE_API_OAUTH_CLIENT_SECRET"] = oauth2["client_secret"]
            if oauth2.get("scope"):
                env["SOURCE_API_OAUTH_SCOPE"] = oauth2["scope"]
        return None, env, False, None
    if source.type == ConnectionType.file:
        files = file_credentials(source)
        # Every file in a profile lives under the same per-profile
        # directory (see create_file_connection_profile) - the parent
        # of any one file's storage_path is that shared directory.
        files_dir = os.path.dirname(files[0]["storage_path"])
        return None, None, False, files_dir
    return postgres_url(source), None, True, None


def _resolve_destination_execution_params(destination):
    """Resolves destination_url, any extra env vars generated code needs
    to write to the destination, and needs_dest_db - dispatched by
    destination type, mirrors _resolve_source_execution_params exactly,
    just for the write side instead of the read side. Every destination
    before Redis was silently assumed to be Postgres everywhere
    destination_url got resolved directly as postgres_url(destination) -
    this is what that assumption needed to become once a non-Postgres
    destination became possible.

    Returns (destination_url, extra_env, needs_dest_db). For Postgres,
    destination_url is a real connection string dlt's sqlalchemy
    destination uses directly, extra_env is empty, needs_dest_db is
    True. For Redis, destination_url is None and needs_dest_db is False -
    there's no dlt destination for Redis at all, so generated code
    connects directly via redis-py instead, reading DEST_REDIS_URL from
    extra_env the same way a Redis SOURCE already reads SOURCE_REDIS_URL.

    needs_dest_db=False matters beyond just "don't pass a meaningless
    URL" - confirmed directly against heal_pipeline.py: without it,
    _resolve_sandbox_db_urls() silently fell back through
    os.environ.get("DEST_DB_URL") to get_db_url(), which resolves to
    this app's OWN control-plane database - a Redis-destination sandbox
    would otherwise get handed real credentials to pulse_audit for no
    reason at all. heal_pipeline.py now has its own needs_dest_db
    parameter specifically to prevent this; it must be threaded through
    from here to every call site that uses this function's return value.
    """
    if destination.type == ConnectionType.redis:
        return None, {"DEST_REDIS_URL": redis_url(destination)}, False
    return postgres_url(destination), {}, True


class PriorTestDataCleanupError(Exception):
    """A genuine failure while cleaning up a prior test's real destination
    rows - distinct from the expected, common case of the table having
    never existed yet, which is not an error at all.
    """


def _cleanup_prior_test_data(destination_url: str, destination_table: str, pipeline_name: str) -> None:
    """Removes whatever a PREVIOUS, separate test attempt for this exact
    file/pipeline already wrote to the real destination, before a new
    test attempt runs.

    Sandbox testing has only ever provided process isolation (the
    generated code can't touch the host machine), never data isolation
    - dlt genuinely writes to the real destination on every successful
    run, independent of whether a human later approves or rejects that
    code. A rejected attempt's successfully-loaded rows otherwise just
    sit there, unconstrained, ready to corrupt the NEXT attempt's
    merge/dedup behavior against genuinely stale data - which is
    exactly what happened here: a first attempt wrote 100 rows via
    append with no primary key, then got rejected, and a second attempt
    switching to merge would have been reconciling against that
    unconstrained leftover data.

    Deliberately narrow in scope: only removes rows whose _dlt_load_id
    came from THIS pipeline_name's own prior loads, identified via
    dlt's own _dlt_loads bookkeeping table (which records pipeline_name
    per load) - never a blind TRUNCATE of the whole table.
    destination_table names are AI-generated and not guaranteed unique
    across pipelines; a blind truncate could destroy a DIFFERENT,
    already-approved and scheduled pipeline's real live data if two
    pipelines ever happened to share a table name. pipeline_name is
    computed the same way generate_pipeline.py's own system prompt
    requires dlt.pipeline(pipeline_name=...) to be set - the bare
    destination table name, with no schema prefix - so this reliably
    matches whatever the generated code itself actually used.

    A missing _dlt_loads table (this destination's very first test
    attempt ever) is the expected, common case, not an error - checked
    explicitly via information_schema first, rather than inferred by
    catching a broad exception class that could otherwise mask a real
    permissions or connection problem as if it were simply "nothing to
    clean up". Any other failure raises PriorTestDataCleanupError,
    stopping the caller from proceeding into a new test against
    potentially-contaminated prior data.
    """
    schema, table = destination_table.split(".", 1) if "." in destination_table else ("public", destination_table)
    print(f"[Cleanup] Checking for prior test data from pipeline '{pipeline_name}' in \"{schema}\".\"{table}\"...")
    engine = sa.create_engine(destination_url)
    try:
        with engine.begin() as conn:
            loads_table_exists = conn.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = '_dlt_loads')"
                ),
                {"schema": schema},
            ).scalar()
            if not loads_table_exists:
                print(f"[Cleanup] No _dlt_loads table in schema \"{schema}\" yet - first test ever for this destination, nothing to clean up.")
                return

            result = conn.execute(
                # _dlt_loads has no pipeline_name column at all - confirmed
                # directly against a real table (\d _dlt_loads showed
                # load_id/schema_name/status/inserted_at/schema_version_hash,
                # nothing else). schema_name is what actually needs
                # matching here; it happens to equal pipeline_name in this
                # codebase specifically because no pipeline ever passes a
                # separate schema= to dlt.pipeline() - confirmed directly
                # too (SELECT schema_name FROM _dlt_loads showed exactly
                # this file's own pipeline_name on all of its real prior
                # loads, and a genuinely different pipeline's own name on
                # an unrelated row, proving the match is neither too
                # broad nor too narrow).
                sa.text(f'SELECT load_id FROM "{schema}"."_dlt_loads" WHERE schema_name = :pipeline_name'),
                {"pipeline_name": pipeline_name},
            )
            load_ids = [row[0] for row in result]
            if not load_ids:
                print(f"[Cleanup] No prior loads recorded under pipeline_name='{pipeline_name}' - nothing to clean up.")
                return

            # A plain sa.text() parameter bound to a raw Python list is
            # NOT reliably handled by SQLAlchemy's binding layer without
            # explicit configuration - confirmed directly against real
            # SQLAlchemy 2.0 reports of this exact pattern silently
            # failing or erroring. bindparam(..., expanding=True) is the
            # verified-correct way to bind a variable-length list into
            # an IN clause through raw text() SQL.
            delete_result = conn.execute(
                sa.text(f'DELETE FROM "{schema}"."{table}" WHERE _dlt_load_id IN :load_ids').bindparams(
                    bindparam("load_ids", expanding=True, value=load_ids)
                ),
            )
            print(f"[Cleanup] Removed {delete_result.rowcount} row(s) from {len(load_ids)} prior load(s) under pipeline_name='{pipeline_name}'.")
    except Exception as exc:
        raise PriorTestDataCleanupError(
            f"Could not clean up prior test data in '{schema}.{table}' for pipeline '{pipeline_name}': {exc}"
        ) from exc
    finally:
        engine.dispose()


@celery_app.task(name="generation_tasks.run_pipeline_generation")
def run_pipeline_generation(pipeline_id: str, version_id: str, goal: str, max_retries: int, actor_id: str) -> None:
    """Task counterpart of generate_and_test_pipeline's slow path (AI
    generation, then sandbox execution via execute_with_self_healing).
    pipeline_id/version_id/actor_id are strings, not uuid.UUID - Celery
    task arguments are JSON-serialized, and JSON has no native UUID
    type; each is converted back to uuid.UUID immediately below.
    actor_id is NOT a secret (just an id identifying who to attribute
    the completion audit-log entry to), so passing it as a task
    argument is fine - unlike decrypted credentials, there's no
    exposure concern here.
    """
    with SessionLocal() as db:
        pipeline_uuid = uuid.UUID(pipeline_id)
        version_uuid = uuid.UUID(version_id)
        actor_uuid = uuid.UUID(actor_id)
        version = db.get(PipelineVersion, version_uuid)
        pipeline = db.get(Pipeline, pipeline_uuid)
        if version is None or pipeline is None:
            # Deleted between the endpoint dispatching this and the
            # worker actually picking it up - nothing left to attach a
            # result to. Same class of data-integrity edge case as
            # schedule_service.py's "version no longer exists" handling.
            return
        try:
            _run_pipeline_generation_locked(db, pipeline, version, goal, max_retries, actor_uuid)
        finally:
            # Always clears, even if something above raised
            # unexpectedly - a version must never get permanently stuck
            # showing "in progress" because of an unhandled exception.
            version.generation_in_progress = False
            db.commit()


def _run_pipeline_generation_locked(db, pipeline, version, goal: str, max_retries: int, actor_id: uuid.UUID) -> None:
    from models import ConnectionProfile
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                          status=RunStatus.failed,
                          started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                          error_output="Generation failed: source or destination connection profile no longer exists.")
        db.add(run)
        version.review_status = PipelineVersionReviewStatus.testing
        _audit(db, actor_id, "pipeline.generation_failed", "pipeline", pipeline.id)
        db.commit()
        return

    from generate_pipeline import generate_pipeline
    try:
        generated = generate_pipeline(source.schema_metadata_json, goal)
        code = generated.get("code")
        if not code:
            raise ValueError("AI response did not include pipeline code")
    except Exception as exc:
        # A hard generation failure (not a sandbox failure) still needs
        # a PipelineRun row - the frontend's poll is watching for one to
        # appear as its "this attempt is over" signal, and without one
        # here, a generation-only failure would poll forever.
        run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                          status=RunStatus.failed,
                          started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                          error_output=f"Pipeline generation failed: {exc}")
        db.add(run)
        version.review_status = PipelineVersionReviewStatus.testing
        _audit(db, actor_id, "pipeline.generation_failed", "pipeline", pipeline.id)
        db.commit()
        return

    version.generated_code = code
    pipeline.generated_code = code
    db.flush()

    destination_table = generated.get("destination_table", "")
    started_at = datetime.now(timezone.utc)
    try:
        destination_url, dest_extra_env, needs_dest_db = _resolve_destination_execution_params(destination)
        source_url, extra_env, needs_source_db, source_files_dir = _resolve_source_execution_params(source)
        extra_env = {**(extra_env or {}), **dest_extra_env}

        # Both the cleanup step and the quality check below run raw SQL
        # directly against the destination (a _dlt_loads lookup, a
        # DELETE, a SELECT COUNT) - genuinely Postgres/dlt-SQL-destination
        # specific, with no Redis equivalent designed yet. A Redis
        # destination test run does NOT get automatic prior-test-data
        # cleanup the way a Postgres one does - a known, real gap, not
        # a silent omission.
        if destination.type == ConnectionType.postgres:
            # See _cleanup_prior_test_data's own docstring for the full
            # reasoning - a prior rejected/failed test attempt may already
            # have written real rows to this same real destination table,
            # and a new attempt (which could use a different write
            # disposition, e.g. switching to merge) needs to start clean
            # rather than silently reconcile against that leftover data.
            _cleanup_prior_test_data(destination_url, destination_table, destination_table.split(".", 1)[-1])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
            script.write(code)
            script_path = script.name
        try:
            from heal_pipeline import execute_with_self_healing
            success, attempts, logs, final_code = execute_with_self_healing(
                script_path, source.schema_metadata_json, max_retries, source_url, destination_url,
                extra_env=extra_env, needs_source_db=needs_source_db, source_files_dir=source_files_dir,
                needs_dest_db=needs_dest_db,
            )
            version.generated_code = final_code
            pipeline.generated_code = final_code
        finally:
            os.unlink(script_path)
    except CredentialResolutionError as exc:
        success, attempts = False, 0
        logs = f"Could not resolve connection credentials: {exc}"
    except PriorTestDataCleanupError as exc:
        success, attempts = False, 0
        logs = str(exc)
    except Exception as exc:
        success, attempts = False, 0
        logs = f"Unexpected error during sandbox test: {exc}"

    quality_result = None
    row_count = None
    if success and destination.type == ConnectionType.postgres:
        try:
            from data_quality import run_quality_checks
            quality_schema, quality_table = _split_destination_table(
                generated.get("destination_dataset", ""), generated.get("destination_table", ""),
            )
            quality_result = run_quality_checks(destination_url, quality_schema, quality_table)
            row_count = quality_result.get("row_count")
        except Exception as exc:
            quality_result = {"checked": False, "reason": f"Quality check itself failed: {exc}"}

    run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                      status=RunStatus.success if success else RunStatus.failed,
                      started_at=started_at, finished_at=datetime.now(timezone.utc),
                      log_output=logs if success else None, error_output=None if success else logs,
                      row_count=row_count, quality_checks=quality_result)
    db.add(run)
    version.review_status = PipelineVersionReviewStatus.pending_review if success else PipelineVersionReviewStatus.testing
    _audit(db, actor_id, "pipeline_version.ready_for_review" if success else "pipeline_version.test_failed", "pipeline_version", version.id)
    db.commit()


@celery_app.task(name="generation_tasks.run_file_generation")
def run_file_generation(file_id: str, max_retries: int, actor_id: str) -> None:
    """Task counterpart of generate_and_test_file's slow path. Unlike
    the pipeline-level task, this re-derives schema_for_generation and
    file_goal itself rather than receiving them as arguments - both are
    cheaply reconstructible from file_id alone via the same DB reads
    the endpoint already did, and re-deriving means the task always
    works from what's actually in the database right now, not a
    snapshot passed through the queue that could go stale.
    """
    with SessionLocal() as db:
        file = db.get(PipelineVersionFile, uuid.UUID(file_id))
        if file is None:
            return
        version = db.get(PipelineVersion, file.pipeline_version_id)
        pipeline = db.get(Pipeline, version.pipeline_id) if version else None
        if version is None or pipeline is None:
            return
        try:
            _run_file_generation_locked(db, pipeline, version, file, max_retries, uuid.UUID(actor_id))
        finally:
            file.generation_in_progress = False
            db.commit()


def _run_file_generation_locked(db, pipeline, version, file: PipelineVersionFile, max_retries: int, actor_id: uuid.UUID) -> None:
    from models import ConnectionProfile
    source = db.get(ConnectionProfile, pipeline.source_connection_id)
    destination = db.get(ConnectionProfile, pipeline.destination_connection_id)
    if source is None or destination is None:
        run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                          pipeline_version_file_id=file.id,
                          status=RunStatus.failed,
                          started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                          error_output="Generation failed: source or destination connection profile no longer exists.")
        db.add(run)
        file.review_status = PipelineVersionReviewStatus.testing
        _audit(db, actor_id, "pipeline_version_file.generation_failed", "pipeline_version_file", file.id)
        db.commit()
        return

    try:
        destination_url, dest_extra_env, needs_dest_db = _resolve_destination_execution_params(destination)
        source_url, extra_env, needs_source_db, source_files_dir = _resolve_source_execution_params(source)
        extra_env = {**(extra_env or {}), **dest_extra_env}
    except CredentialResolutionError as exc:
        run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                          pipeline_version_file_id=file.id,
                          status=RunStatus.failed,
                          started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                          error_output=f"Could not resolve connection credentials: {exc}")
        db.add(run)
        file.review_status = PipelineVersionReviewStatus.testing
        _audit(db, actor_id, "pipeline_version_file.generation_failed", "pipeline_version_file", file.id)
        db.commit()
        return

    # Re-derive schema_for_generation exactly as the endpoint did -
    # earlier approved files' real destination schemas, introspected
    # fresh, plus the original source schema. See review_api.py's
    # generate_and_test_file for the full reasoning; unchanged here.
    #
    # Genuinely Postgres-specific (introspect_schema does SQLAlchemy
    # reflection) - the destination connection is set once at the
    # PIPELINE level, so every file in a build plan shares the same
    # destination type, and for a Redis destination there is no SQL
    # schema here to introspect at all. Skipped entirely for a
    # non-Postgres destination rather than attempted and failed.
    schema_for_generation = dict(source.schema_metadata_json)
    if file.file_order > 1 and destination.type == ConnectionType.postgres:
        earlier_files = db.scalars(
            select(PipelineVersionFile).where(
                PipelineVersionFile.pipeline_version_id == version.id,
                PipelineVersionFile.file_order < file.file_order,
                PipelineVersionFile.review_status == PipelineVersionReviewStatus.approved,
            )
        ).all()
        destination_schemas = {
            f.destination_table.split(".", 1)[0]
            for f in earlier_files
            if f.destination_table and "." in f.destination_table
        }
        for schema_name in destination_schemas:
            try:
                schema_for_generation.update(
                    introspect_schema(db_url=destination_url, schema_name=schema_name, sample_rows=0)
                )
            except Exception as exc:
                print(f"[Warning] Could not introspect destination schema '{schema_name}' for upstream file context: {exc}")

    file_goal = (
        f"{pipeline.project.goal_description}\n\n"
        f"You are generating ONE file within an approved multi-file build plan: "
        f"'{file.file_name}'. Purpose: {file.purpose}. This file must read ONLY "
        f"from: {', '.join(file.reads_from) or '(the original source only)'}. "
        f"Write its output to a table named exactly '{file.destination_table}'. "
        f"Do not implement any other file's purpose - only this one."
    )
    if version.uses_pinned_schema and file.schema_ddl:
        # The actual enforcement mechanism, confirmed directly against
        # dlt's own documentation rather than assumed: dlt does NOT
        # reflect an existing table's real structure from the database
        # on a pipeline's first run against it - dlt's own docs state
        # plainly that "dlt will only be aware of columns that exist"
        # in ITS OWN tracked schema state, which starts genuinely empty
        # for a brand-new pipeline_name (exactly what every fresh file
        # generation here creates). schema_contract={"columns": "freeze"}
        # ALONE, with nothing else, would be a no-op on that first run -
        # there is nothing yet for it to freeze against, so it would
        # simply accept whatever the first run's own data happens to
        # produce as the new baseline, not this file's real pinned
        # structure.
        #
        # What actually closes that gap - confirmed directly against a
        # real, working example in dlt's own Resource documentation -
        # is an EXPLICIT columns={...} declaration on @dlt.resource,
        # which gives dlt the expected schema upfront rather than
        # requiring a prior run to have established it. schema_contract
        # then enforces AGAINST that declared schema. Both pieces are
        # required together; either alone does not achieve what this
        # feature needs.
        #
        # Also confirmed directly: schema_contract is a real parameter
        # on @dlt.resource, @dlt.source, and pipeline.run() - NOT on
        # dlt.pipeline() itself, which accepts no such argument.
        file_goal = (
            f"{file_goal}\n\nThis file's destination table ALREADY EXISTS in the "
            f"real database with EXACTLY this approved structure - it was created "
            f"before this code was ever generated, and is not yours to redefine:\n\n"
            f"{file.schema_ddl}\n\n"
            f"Your generated code must conform to this exact structure. This requires "
            f"BOTH of the following together on your @dlt.resource - neither alone is "
            f"enough:\n"
            f"1. An explicit columns={{...}} declaration listing EVERY column above by "
            f"name, using dlt's own data_type vocabulary (text, bigint, double, bool, "
            f"timestamp, date, time, decimal, json - NOT raw Postgres type names like "
            f"'double precision' or 'jsonb'; map the pinned Postgres type to the closest "
            f"dlt type yourself) and nullable matching what's pinned above. For example: "
            f'columns={{"customer_id": {{"data_type": "text", "nullable": False}}, '
            f'"age": {{"data_type": "bigint", "nullable": True}}}}. Without this, dlt has '
            f"no prior knowledge of the existing table's real structure to enforce "
            f"against, since this is a brand-new pipeline with no run history.\n"
            f"2. schema_contract={{\"columns\": \"freeze\", \"data_type\": \"freeze\"}} - also "
            f"on @dlt.resource (or @dlt.source if this file defines one), never on "
            f"dlt.pipeline() itself, which does not accept this parameter at all.\n"
            f"Every field you yield must map to one of the columns already named above; "
            f"do not add, rename, or drop any of them."
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
        run = PipelineRun(pipeline_id=pipeline.id, pipeline_version_id=version.id,
                          pipeline_version_file_id=file.id,
                          status=RunStatus.failed,
                          started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                          error_output=f"File generation failed: {exc}")
        db.add(run)
        file.review_status = PipelineVersionReviewStatus.testing
        _audit(db, actor_id, "pipeline_version_file.generation_failed", "pipeline_version_file", file.id)
        db.commit()
        return

    file.generated_code = code
    db.flush()

    started_at = datetime.now(timezone.utc)
    try:
        # See _cleanup_prior_test_data's own docstring for the full
        # reasoning - a prior rejected/failed test attempt on this same
        # file may already have written real rows to this same real
        # destination table. Genuinely Postgres-specific (raw SQL
        # against _dlt_loads) - skipped for a Redis destination, a
        # known, real gap rather than a silent omission.
        if destination.type == ConnectionType.postgres:
            _cleanup_prior_test_data(destination_url, file.destination_table, file.destination_table.split(".", 1)[-1])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as script:
            script.write(code)
            script_path = script.name
        try:
            from heal_pipeline import execute_with_self_healing
            success, attempts, logs, final_code = execute_with_self_healing(
                script_path, schema_for_generation, max_retries, source_url, destination_url,
                extra_env=extra_env, needs_source_db=needs_source_db, source_files_dir=source_files_dir,
                needs_dest_db=needs_dest_db,
            )
            file.generated_code = final_code
        finally:
            os.unlink(script_path)
    except PriorTestDataCleanupError as exc:
        success, attempts = False, 0
        logs = str(exc)
    except Exception as exc:
        success, attempts = False, 0
        logs = f"Unexpected error during sandbox test: {exc}"

    quality_result = None
    row_count = None
    if success and destination.type == ConnectionType.postgres:
        try:
            from data_quality import run_quality_checks
            quality_schema, quality_table = _split_destination_table(
                generated.get("destination_dataset", ""), file.destination_table,
            )
            quality_result = run_quality_checks(destination_url, quality_schema, quality_table)
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
    _audit(db, actor_id,
           "pipeline_version_file.ready_for_review" if success else "pipeline_version_file.test_failed",
           "pipeline_version_file", file.id)
    db.commit()
