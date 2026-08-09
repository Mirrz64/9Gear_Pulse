# 9gear Pulse — Product Requirements Document (PRD)

## 1. Product Overview
9gear Pulse is an AI-assisted data pipeline builder. A user describes a
goal in plain English and connects a source and destination database;
the platform generates, tests, and (on approval) schedules a working
ETL/ELT pipeline — without the user hand-writing extraction, transform,
or load code.

## 2. Problem Statement
Building even a simple pipeline today requires a data engineer: writing
extraction scripts, handling schema drift, wiring transforms, and
setting up scheduling/monitoring. Small teams and founders without a
dedicated data engineer either go without reliable data pipelines or
burn disproportionate time building them by hand.

## 3. Positioning (v1)
9gear Pulse v1 is **not** a full data platform — it's a focused wedge:
AI-generated pipelines for a single source/destination pair (Postgres →
Postgres first), proven end-to-end, before expanding to more connectors,
a semantic layer, or BI export. Depth over breadth for the first release.

## 4. Target User (v1)
- Founders/small teams who need a working pipeline but don't have a data
  engineer on staff.
- Data/analytics engineers at small-to-mid companies who want to
  offload the repetitive parts of pipeline-building (boilerplate
  extraction/load code) and focus review time on correctness, not typing.

## 5. Core User Story
"As a user, I describe what I want in plain English, point the tool at
my source and destination, and get a working, reviewed pipeline running
on a schedule — without writing the extraction/load code myself."

## 6. V1 Scope (Must-Have)
- Connection Profile manager (encrypted credential storage; AI never
  sees raw credentials — see Technical Requirements Doc).
- Postgres schema introspection.
- AI pipeline generation (goal + schema metadata → runnable `dlt`/Python
  script).
- Docker sandbox execution against sample data.
- Self-healing retry loop on execution errors (bounded retries).
- Human review UI: code diff + run log + data preview, approve/reject.
- Basic cron-based scheduler for approved pipelines.
- Audit log of every generated version and every approval action.

## 7. Explicitly Out of Scope for V1
- Autonomous multi-cloud provisioning (Terraform `apply` without a human
  review gate).
- Third-party BI export (Power BI/Tableau connectors).
- Semantic layer / auto-generated metrics.
- Native mobile apps.
- Multi-tenant org hierarchy / granular RBAC.

These are natural v2+ features once the core generation loop is proven
reliable — see the Growth Plan for sequencing.

## 8. Success Metrics
- **Generation accuracy**: % of AI-generated pipelines a user approves
  with zero or minor edits (the single most important number — it's
  what everything else is built on).
- **Time-to-first-successful-pipeline**: from account creation to first
  approved, scheduled pipeline.
- **Self-heal rate**: % of initial generation errors resolved
  automatically within the retry budget, vs. requiring manual fixes.
- **Retention of scheduled pipelines**: % still running (not disabled or
  deleted) 30 days after approval — a proxy for "did this actually work
  in production."

## 9. Key Risks
- **Generation reliability** — if the AI produces broken or subtly wrong
  pipelines too often, the review step becomes a bottleneck instead of a
  safeguard, and the product loses its core value proposition.
- **Credential security** — a single incident involving credential
  exposure would be existential for a product whose entire pitch is
  "trust us with your database connections." See the Technical
  Requirements Document's security section.
- **Schema diversity** — real-world Postgres schemas (weird naming,
  missing keys, denormalized tables) will stress-test generation
  accuracy far more than a clean demo schema will.
