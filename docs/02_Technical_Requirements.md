# 9gear Pulse — Technical Requirements Document

## 1. Architecture Summary

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js + Tailwind + shadcn/ui | Dashboard, connection manager, review UI |
| Backend API | FastAPI (Python) | Async; matches existing backend/venv setup |
| Database | PostgreSQL | Stores projects, connections, pipelines, audit log |
| File/artifact storage | S3 or Cloudflare R2 | Generated pipeline code versions, run logs |
| AI | Claude API (`claude-sonnet-5`) | Pipeline generation + self-healing fix loop |
| Sandbox execution | Docker (ephemeral containers) | Runs generated pipelines against sample data |
| Scheduling | APScheduler (v1) → Celery/Redis (later) | Cron-based pipeline runs |
| Auth | Clerk or Auth0 | Don't hand-roll auth for a credentials-handling product |

## 2. Functional Requirements

- Introspect a Postgres schema (tables, columns, types, row counts,
  optional sample rows) via `information_schema`.
- Accept a natural-language goal and generate a runnable `dlt`/Python
  pipeline script from goal + schema metadata.
- Execute generated code in an isolated, ephemeral Docker container
  against sample/staging data only.
- Capture `stderr` on failure and feed it back into a bounded
  self-healing retry loop (recommend max 3 attempts).
- Present a human reviewer with a code diff, run log, and data preview
  before any pipeline is approved for scheduling.
- Schedule approved pipelines on a cron expression and record every run
  (status, duration, row counts, errors).

## 3. Non-Functional Requirements

- **Security**: See §4 below — this is the requirement that overrides
  convenience anywhere the two conflict.
- **Performance**: schema introspection should complete in a few
  seconds for a typical schema (tens of tables); pipeline generation is
  bound by AI API latency, not a hard target.
- **Reliability**: self-healing retries are bounded — an unbounded loop
  on a bad prompt/schema combination must not be possible.
- **Auditability**: every generated pipeline version and every human
  approval/rejection is logged with actor, timestamp, and diff.

## 4. Security Requirements (Non-Negotiable)

- Credentials are encrypted at rest (KMS-backed or a secrets manager)
  and are **never** included in any AI prompt or AI response — the AI
  only receives a connection ID and derived schema metadata.
- The backend (not the LLM) resolves connection IDs to real credentials
  and injects them into the sandbox container as environment variables
  at run time.
- Sandbox containers have network egress restricted to only the
  declared source/destination endpoints — no general internet access.
- Test/sandbox runs operate on sampled data only; full production
  tables are touched only after explicit human promotion.
- Recommend (and where possible enforce) least-privilege, read-only
  database users for source connections.

## 5. Integration Requirements

| Integration | V1 status | Auth pattern |
|---|---|---|
| PostgreSQL (source & destination) | **In scope** | Connection Profile, encrypted credentials |
| Snowflake | v1.x | Key-pair auth preferred over password |
| BigQuery / S3 | v1.x | Service account, scoped |
| GitHub (push generated code) | v1.x | GitHub App, branch + PR, never direct-to-main |
| Azure / GCP (provisioning) | v2 | Service principal / service account, plan-before-apply gate |

## 6. Data Model

See `04_Database_Schema.md` for the full schema — `projects`,
`connection_profiles`, `pipelines`, `pipeline_runs`, `schedules`,
`audit_log`.
