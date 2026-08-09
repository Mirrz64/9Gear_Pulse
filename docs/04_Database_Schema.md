# 9gear Pulse — Database Schema

## Entity Overview

```
projects              (id, owner_id, name, goal_description, status)
connection_profiles    (id, owner_id, type, encrypted_credentials, schema_metadata_json, last_introspected_at)
pipelines              (id, project_id, source_connection_id, destination_connection_id,
                        generated_code, version, status: draft|testing|approved|scheduled)
pipeline_runs          (id, pipeline_id, started_at, finished_at, status, log_output, error_output, row_count)
schedules              (id, pipeline_id, cron_expression, next_run_at)
audit_log              (id, actor_id, action, entity_type, entity_id, timestamp)
```

## Table Detail

### `projects`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| owner_id | uuid FK → users | |
| name | text | |
| goal_description | text | the plain-English goal as entered |
| status | enum | active / archived |

### `connection_profiles`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| owner_id | uuid FK → users | |
| type | enum | postgres, snowflake, bigquery, s3, ... |
| encrypted_credentials | bytea | KMS-encrypted; never exposed to the AI layer |
| schema_metadata_json | jsonb | last introspected schema summary — this, not the credential, is what the AI sees |
| last_introspected_at | timestamptz | |

### `pipelines`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK → projects | |
| source_connection_id | uuid FK → connection_profiles | |
| destination_connection_id | uuid FK → connection_profiles | |
| generated_code | text | latest version of the generated script |
| version | int | increments on regeneration/edit |
| status | enum | draft / testing / approved / scheduled |

### `pipeline_runs`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| pipeline_id | uuid FK → pipelines | |
| started_at / finished_at | timestamptz | |
| status | enum | success / failed / retrying |
| log_output / error_output | text | |
| row_count | int | rows processed, for sanity-checking scale |

### `schedules`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| pipeline_id | uuid FK → pipelines | |
| cron_expression | text | |
| next_run_at | timestamptz | |

### `audit_log`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| actor_id | uuid FK → users | |
| action | text | e.g. "pipeline.approved", "connection.created" |
| entity_type / entity_id | text / uuid | what was acted on |
| timestamp | timestamptz | |

## Design Notes
- `schema_metadata_json` on `connection_profiles` is deliberately the
  only schema-shaped data the AI generation layer reads — it's cached
  there so you're not re-introspecting on every generation call.
- `pipelines.version` + `audit_log` together give you a full history of
  what the AI generated, what a human changed, and who approved it —
  this is the paper trail that matters if a pipeline ever misbehaves in
  production.
- Consider a `pipeline_versions` table instead of overwriting
  `generated_code` in place once you need to diff across versions in
  the review UI — worth doing before the review UI ships, cheap to add
  now, annoying to retrofit later.
