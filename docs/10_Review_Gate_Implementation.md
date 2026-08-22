# Review / Approval Gate — Backend Setup

The review gate is served from `/api/v2` and uses the PostgreSQL commercial
schema. It is intentionally unavailable when `DATABASE_URL` points to the
legacy SQLite prototype database.

## Start the stack and migrate

From the repository root, start Postgres and the API:

```powershell
docker compose up --build -d
docker compose exec backend alembic -c alembic.ini upgrade head
```

Confirm the review routes are registered at `http://localhost:8000/docs`.
For a local (non-Docker) backend, set this exact connection string before
running Alembic or Uvicorn:

```powershell
$env:DATABASE_URL = 'postgresql://pulse:pulse_secure_password@localhost:5433/pulse_audit'
Set-Location backend
alembic -c alembic.ini upgrade head
uvicorn app:app --reload --port 8000
```

Never apply this migration to `backend/9gear_pulse.db`; that is the legacy
SQLite prototype and cannot represent this schema.

### Preserve prototype audit history

The old dashboard used `backend/9gear_pulse.db`, while the new control plane
uses PostgreSQL. To copy the existing prototype audit records once, run:

```powershell
docker compose exec backend python import_legacy_audit_logs.py
```

## Gate workflow

The temporary `actor_id` field is the user's UUID until Clerk/Auth0 is added.
It is used to prove the user owns the pipeline's project.

1. Generation or a human edit creates a new immutable version:

```http
POST /api/v2/pipelines/{pipeline_id}/versions
{
  "actor_id": "{user_id}",
  "generated_code": "# revised pipeline code"
}
```

2. The sandbox worker records the outcome against *that version*. A success
moves it to `pending_review`; a failure remains in `testing`.

```http
POST /api/v2/pipeline-versions/{version_id}/test-result
{
  "actor_id": "{user_id}",
  "status": "success",
  "log_output": "Sandbox passed",
  "row_count": 42
}
```

3. The UI fetches the review payload (current and prior code, runs, and audit
history), then the reviewer approves or rejects it.

```http
GET /api/v2/pipelines/{pipeline_id}/review?actor_id={user_id}

POST /api/v2/pipeline-versions/{version_id}/approve
{
  "actor_id": "{user_id}",
  "comment": "Validated the destination mapping."
}
```

4. Only the exact, current approved version can be scheduled. The schedule
stores its version ID so future edits cannot silently change what runs.

```http
POST /api/v2/pipelines/{pipeline_id}/schedule
{
  "actor_id": "{user_id}",
  "cron_expression": "0 2 * * *"
}
```

## Non-negotiable rules enforced by the API

- A new edit creates a new draft and resets the pipeline to `draft`.
- An approval requires a successful sandbox run for that exact version.
- An old version cannot be approved after a newer version exists.
- A pipeline cannot be scheduled until its current version is approved.
- Approval and rejection are append-only records in `pipeline_reviews`, with
matching entries in `audit_log`.

## Next integration task

Connect `run_end_to_end_pipeline` to create a `PipelineVersion` and call the
test-result service with the sandbox log, row count, and final status. Do not
call the legacy `/api/schedule` endpoint for commercial pipelines: it accepts
a free-text goal and bypasses this gate.
