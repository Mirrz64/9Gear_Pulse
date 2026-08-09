# 9gear Pulse — MVP User Journey Flow

## Overview
The entire v1 journey is designed around one loop: describe → generate →
test → review → approve → schedule. Every step is visible to the user;
nothing runs against real production data without a human okaying it
first.

## Step-by-Step Flow

**1. Sign up / log in**
Standard auth (Clerk/Auth0). No project data required yet.

**2. Create a Connection Profile**
User fills a secure form: name, type (Postgres for v1), host, port,
database, credentials. Credentials are encrypted immediately; this
screen is deliberately separate from any AI-facing chat interface.

**3. Create a Project & describe the goal**
Plain English, e.g. *"Pull orders from our Postgres orders table and
build a daily revenue-by-region summary in our reporting database."*
User selects source and destination Connection Profiles.

**4. System introspects schemas**
Backend queries `information_schema` on both connections. User sees a
quick preview of what was found (tables, columns, row counts) — a
natural checkpoint to confirm the right database was connected.

**5. AI generates the pipeline**
Goal + schema metadata (never credentials) go to the AI. It returns a
structured pipeline: extraction config, transform logic, load script.

**6. Sandbox test run**
An ephemeral Docker container runs the generated pipeline against
sampled data. If it fails, the self-healing loop retries automatically
(bounded attempts) before surfacing to the user.

**7. Human review**
User sees: the generated code (diffable), the run log, and a preview of
the resulting output data. Three actions: **Approve**, **Edit &
Approve**, **Reject**.

**8. Schedule**
On approval, the user sets a schedule (or runs once). The pipeline is
registered and every future run is logged.

**9. Monitor**
Dashboard shows pipeline status, last run result, and history. Failed
scheduled runs surface clearly — this is where trust is either built or
lost over time.

## What's Deliberately *Not* in the V1 Flow
- No autonomous production writes without step 7's human review.
- No infrastructure provisioning (Terraform) in this flow at all —
  that's a separate, later capability with its own review gate.
- No multi-step conversational refinement loop with the AI in v1 — one
  goal in, one generated pipeline out, human edits by hand if needed.
  Conversational iteration is a reasonable v1.x addition once the
  single-shot generation accuracy is proven.
