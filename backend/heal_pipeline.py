import os
import json
import docker
import anthropic
import openai
from dotenv import load_dotenv
from pydantic import BaseModel

from introspect import get_db_url

load_dotenv(override=False)

# Initialize clients if keys exist in environment
anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class HealedPipeline(BaseModel):
    fixed_code: str
    root_cause: str
    changes_made: str


HEALER_SYSTEM_PROMPT = """You are an expert Python data engineering agent specializing in `dlt` and data pipelines.
You are given a broken Python ETL script, schema metadata of the source, and the runtime error/traceback produced when executing it inside a Docker container.

Your job is to fix the code so that it executes without errors.
- Preserve the overall pipeline logic and business goal.
- Use actual existing tables/fields defined in the schema summary.
- The source may be a DATABASE, a REST API, one or more UPLOADED FILES, or REDIS - check the schema summary itself for which: an API source's schema entries have "schema": "api"; a file source's schema entries have "schema": "file"; a Redis source's schema entries have "schema": "redis"; a database source has a real schema name (e.g. "public"). Do NOT invent credentials for any of them; keep reading from environment variables:
  - Database source: SOURCE_DB_URL
  - API source: SOURCE_API_BASE_URL, SOURCE_API_AUTH_HEADERS (a JSON object string of header name -> value - json.loads() it; do not expect separate key/header-name/scheme env vars, that pattern no longer exists), SOURCE_API_METHOD ("GET" or "POST", always present - check it, don't assume GET), SOURCE_API_BODY (a JSON object string, meaningful only when SOURCE_API_METHOD is "POST" - use requests.post(url, headers=headers, json=json.loads(body), timeout=...) in that case, requests.get otherwise), SOURCE_API_PAGINATION (present ONLY when this source is paginated - a JSON object string with "style": "offset"/"page"/"cursor" plus that style's own params; absent means a single response has every record already, don't add a pagination loop where none is configured. If a broken pipeline's code has a pagination loop that doesn't match this variable's actual style/param names, or is missing one entirely while SOURCE_API_PAGINATION IS set, that mismatch is very likely the real bug - check this before assuming the fix belongs somewhere else), SOURCE_API_OAUTH_TOKEN_URL/CLIENT_ID/CLIENT_SECRET/SCOPE (present ONLY for OAuth2 client-credentials auth, mutually exclusive with SOURCE_API_AUTH_HEADERS - a token must be fetched fresh via a POST to SOURCE_API_OAUTH_TOKEN_URL with grant_type=client_credentials, used as an Authorization: Bearer header, and refreshed on a 401 rather than assumed valid forever, since a pre-fetched token can easily expire mid-run. A 401 error on an OAuth2 source is very likely a missing or broken refresh-on-401 step, not a credentials problem - check this before assuming client_id/client_secret themselves are wrong)
  - File source: SOURCE_FILES_DIR (a directory containing every uploaded file for this pipeline, named exactly as shown in the schema summary's table names - e.g. os.path.join(os.environ["SOURCE_FILES_DIR"], "orders.csv")). SOURCE_DB_URL and SOURCE_API_BASE_URL will both be absent for a file-source pipeline - do not add either where none exists.
  - Redis source: SOURCE_REDIS_URL (connect with redis.Redis.from_url(...)). Never use client.keys() (blocking, unsafe against a real instance) - always scan_iter(match=<pattern>) instead. Each schema entry's redis_type field (string/hash/list/set/zset/stream) tells you the correct read method - get/hgetall/lrange/smembers/zrange/xrange respectively.
  - GraphQL source: SOURCE_GRAPHQL_ENDPOINT (the one and only endpoint - never construct a different URL per resource), SOURCE_GRAPHQL_QUERY (send verbatim, never rewritten), SOURCE_GRAPHQL_VARIABLES (a JSON object string, json.loads() it). Auth reuses the exact same SOURCE_API_AUTH_HEADERS / SOURCE_API_OAUTH_* variables and handling an API source uses - not a different mechanism. POST {"query": ..., "variables": ...} to the endpoint, then check response_json.get("errors") explicitly before trusting response_json["data"] - GraphQL returns HTTP 200 even when the query itself failed, with the real error living in a separate "errors" field response.raise_for_status() will never catch. A broken GraphQL pipeline that "succeeds" but yields nothing is very likely this exact case, not a connection problem.
  - SOAP source: SOURCE_SOAP_ENDPOINT, SOURCE_SOAP_VERSION ("1.1" or "1.2" - check it, header construction genuinely differs: 1.1 uses Content-Type: text/xml plus a separate, quoted SOAPAction header; 1.2 embeds the action inside Content-Type's own action= parameter instead, no separate header), SOURCE_SOAP_ACTION (may be an empty string), SOURCE_SOAP_BODY (the exact XML envelope, send verbatim). Auth reuses the exact same SOURCE_API_AUTH_HEADERS / SOURCE_API_OAUTH_* variables an API source uses. Parse the XML response with xml.etree.ElementTree (never a client library like zeep - not installed in this sandbox), and explicitly search for a <Fault> element by local tag name (after stripping its XML namespace) BEFORE trusting the response or calling raise_for_status() - real SOAP services are inconsistent about returning a non-200 status for a fault, so the status code alone is not reliable here. A broken SOAP pipeline that "succeeds" but yields nothing, or a KeyError/AttributeError parsing the response, is very likely a missed <Fault> or a wrong namespace-stripped tag name, not a connection problem.
  - Destination: either a database (DEST_DB_URL, always present for a SQL destination) or Redis (DEST_REDIS_URL, connect with redis.Redis.from_url(...) and write with dest_client.hset(key, mapping={...}) for a typical multi-field record - never dlt.pipeline()/pipeline.run() for a Redis destination, there is no dlt destination for it) or Snowflake (DEST_SNOWFLAKE_ACCOUNT/USER/PASSWORD/DATABASE, plus optional WAREHOUSE/ROLE - use dlt's own native dlt.destinations.snowflake(credentials={...}) destination, never the generic SQLAlchemy one; Snowflake's real load mechanism is staged-file bulk loading, not row-by-row SQL inserts, so a broken Snowflake pipeline that's using dlt.destinations.sqlalchemy(...) instead of the native snowflake destination is very likely the actual bug) or S3/MinIO (DEST_S3_BUCKET_URL, DEST_S3_ACCESS_KEY_ID, DEST_S3_SECRET_ACCESS_KEY, plus optional DEST_S3_REGION/DEST_S3_ENDPOINT_URL - use dlt.destinations.filesystem(bucket_url=..., credentials={...}); there is no SQL of any kind here, no CREATE TABLE, nothing SQLAlchemy-compatible - a broken S3 pipeline's fix is never a SQL fix) or Azure Blob Storage (DEST_AZURE_BLOB_BUCKET_URL, DEST_AZURE_BLOB_ACCOUNT_NAME, DEST_AZURE_BLOB_ACCOUNT_KEY - same dlt.destinations.filesystem(...) mechanism as S3, same "no SQL concept at all" reasoning, just different credential field names) or BigQuery (DEST_BIGQUERY_PROJECT_ID/PRIVATE_KEY/CLIENT_EMAIL/LOCATION - use dlt's own native dlt.destinations.bigquery(credentials={...}, location=...) destination, never the generic SQLAlchemy one; authenticated via a service-account key, not a connection string). Check which env var is actually present before assuming a fix belongs in generic dlt/SQLAlchemy code - a broken Redis, Snowflake, S3, Azure Blob, or BigQuery pipeline's fix is almost never a generic SQL issue.
  - Pinned schema (only when the goal itself says this file's destination table already exists with an approved structure, pasted directly into the goal text - not a separate env var): the existing code's @dlt.resource MUST already have BOTH an explicit columns={...} declaration (using dlt's own data_type vocabulary - text/bigint/double/bool/timestamp/date/time/decimal/json, never raw Postgres names like "double precision" or "jsonb") listing every pinned column, AND schema_contract={"columns": "freeze", "data_type": "freeze"} - also on @dlt.resource/@dlt.source, never on dlt.pipeline() itself, which does not accept that parameter at all. A "contract violation" / "column X frozen" error, or a schema-mismatch error where generated code seems to silently invent a column not in the pinned structure, means one of those two pieces is missing or wrong - not a reason to remove either of them or fall back to letting dlt infer its own schema. Removing the columns={...} declaration or the schema_contract to "fix" an error here defeats the entire point of a pinned schema and must never be the fix.
- Only call functions that genuinely exist in the `dlt` public API, or in `requests` for an API source. If you are not certain a function exists, do not use it.
- The destination database engine (SQLite, Postgres, etc.) is not known ahead of time and must never be assumed. If the error is destination/credentials-related, the fix is always the generic SQLAlchemy destination, which auto-detects the right dialect from the connection string - never an engine-specific one like `dlt.destinations.postgres(...)`:

      import sqlalchemy as sa
      dest_engine = sa.create_engine(os.environ["DEST_DB_URL"])
      destination=dlt.destinations.sqlalchemy(dest_engine)

- If the error is `psycopg2.errors.InvalidTableDefinition: multiple primary keys for table "..." are not allowed`, do NOT fix this by checking for an existing constraint by a specific name (e.g. `WHERE conname = 'pk_my_table'`) - that does not prevent the error, because PostgreSQL disallows a second primary key under ANY name, including an auto-generated one like `<table>_pkey` that may already be on the table from its initial creation. Check `pg_constraint.contype = 'p'` scoped to the table via `conrelid = '<schema>.<table>'::regclass` instead - that correctly detects any existing primary key regardless of its name, before attempting to add a new one. This does not apply to unique or check constraints, which Postgres allows multiple of per table - name-based existence checks are correct for those.
- Return ONLY valid JSON matching this exact structure with no extra text or explanations:

{
  "fixed_code": "string containing the complete corrected Python script",
  "root_cause": "brief plain text explanation of what caused the failure",
  "changes_made": "summary of fixes applied"
}
"""

def _resolve_sandbox_db_urls(script_dir: str, source_db_url: str = None, dest_db_url: str = None, needs_source_db: bool = True, needs_dest_db: bool = True):
    """The sandbox container is fully isolated: it can't reach 'localhost'
    on the host machine, and it has no access to the host filesystem unless
    something is explicitly mounted in. This used to hardcode a Postgres
    connection (postgres:devpass@host.docker.internal:5433/testdb) that
    matched nothing in this project's actual config.

    Instead, this resolves whatever SOURCE_DB_URL/DEST_DB_URL (or the same
    DATABASE_URL/PG_* fallback introspect.py already uses) is genuinely
    configured, and adapts it so the sandbox container can actually reach
    it - by mounting the file in for SQLite, or swapping 'localhost' for
    the special Docker host DNS name for Postgres.

    needs_source_db=False skips resolving a source URL entirely, rather
    than defaulting to None and silently falling back through
    SOURCE_DB_URL/get_db_url() - which would inject this app's own
    control-plane database as "the source" for a pipeline whose real
    source is an API, not Postgres at all. There genuinely is no
    database URL to give it in that case; source connectivity comes
    entirely through extra_env instead (see run_in_sandbox).

    needs_dest_db=False does the identical thing for the destination
    side, added once a destination could genuinely be non-SQL (Redis) -
    the exact same leak this function already prevented for sources
    was otherwise still wide open here: dest_db_url=None fell through
    to get_db_url(), silently handing a Redis-destination sandbox
    working credentials to this app's own control-plane database for
    no reason at all. Destination connectivity for a non-SQL
    destination comes entirely through extra_env instead, the same way
    a non-SQL source already does.

    Returns (source_url, dest_url, extra_volumes) - either URL is None
    when its corresponding needs_*_db flag is False.
    """
    dest_url = None
    if needs_dest_db:
        # Previously fell back to source_url here, which made sense when
        # every pipeline had a real database source to fall back to. Now
        # that source_url can genuinely be None (an API source has no
        # database URL at all), get_db_url() is the safer fallback - and
        # for the normal Postgres-source case, it resolves to the exact
        # same value source_url would have anyway, so this doesn't change
        # behavior for any real caller (review_api.py always passes both
        # URLs explicitly regardless).
        dest_url = dest_db_url or os.environ.get("DEST_DB_URL") or get_db_url()

    source_url = None
    if needs_source_db:
        source_url = source_db_url or os.environ.get("SOURCE_DB_URL") or get_db_url()

    extra_volumes = {}

    def adapt(url: str) -> str:
        if url.startswith("sqlite"):
            db_filename = url.split("/")[-1]
            host_path = os.path.abspath(os.path.join(script_dir, db_filename))
            container_path = f"/data/{db_filename}"
            if os.path.exists(host_path):
                extra_volumes[host_path] = {"bind": container_path, "mode": "rw"}
            return f"sqlite:///{container_path}"
        # Postgres (or anything else network-based): 'localhost'/'127.0.0.1'
        # on the host isn't reachable from inside the sandbox container.
        return (
            url.replace("localhost", "host.docker.internal")
               .replace("127.0.0.1", "host.docker.internal")
        )

    if dest_url:
        dest_url = adapt(dest_url)
    if source_url:
        source_url = adapt(source_url)

    return source_url, dest_url, extra_volumes


def run_in_sandbox(script_path: str, source_db_url: str = None, dest_db_url: str = None, extra_env: dict = None, needs_source_db: bool = True, source_files_dir: str = None, needs_dest_db: bool = True) -> tuple[bool, str]:
    """Runs the specified script in an isolated Docker container.

    extra_env lets callers inject additional environment variables
    beyond SOURCE_DB_URL/DEST_DB_URL - e.g. SOURCE_API_BASE_URL and
    SOURCE_API_AUTH_HEADERS for a pipeline reading from a REST API source
    instead of Postgres, or DEST_REDIS_URL for a Redis destination.
    Deliberately generic by name (not API-specific) so the next new
    source or destination type doesn't need this function's signature
    touched again.

    source_files_dir is the one exception that DOES need its own
    parameter rather than going through extra_env - a file source needs
    actual filesystem access inside the sandbox, not just an env var.
    This bind-mounts the real host directory the caller resolved (e.g.
    via file_credentials()) directly into the sandbox, exactly the same
    "resolve a real host path, bind-mount it in" pattern
    _resolve_sandbox_db_urls() already uses for SQLite - not a shared
    named Docker volume, because this process runs as bare `uvicorn`
    today, not as a docker-compose service itself, so there's no
    meaningful named volume to share in the first place. If the backend
    is ever containerized too, this bind-mount (like SQLite's) would
    need revisiting the same way.

    needs_dest_db=False (mirroring needs_source_db) skips resolving a
    destination database URL entirely for a non-SQL destination like
    Redis - without it, a Redis-destination sandbox would silently get
    handed this app's own control-plane database's real credentials as
    DEST_DB_URL, a real, unnecessary credential exposure with nothing
    to do with the pipeline actually running.
    """
    abs_path = os.path.abspath(script_path)
    script_dir = os.path.dirname(abs_path) or "."
    image_name = "python:3.10-slim"

    source_url, dest_url, extra_volumes = _resolve_sandbox_db_urls(
        script_dir, source_db_url, dest_db_url, needs_source_db, needs_dest_db,
    )
    env_vars = {}
    if dest_url:
        env_vars["DEST_DB_URL"] = dest_url
    if source_url:
        env_vars["SOURCE_DB_URL"] = source_url
    if extra_env:
        env_vars.update(extra_env)
    if source_files_dir:
        container_files_dir = "/data/source_files"
        extra_volumes[os.path.abspath(source_files_dir)] = {"bind": container_files_dir, "mode": "ro"}
        env_vars["SOURCE_FILES_DIR"] = container_files_dir

    volumes = {abs_path: {'bind': '/app/pipeline.py', 'mode': 'ro'}}
    volumes.update(extra_volumes)

    setup_and_run_cmd = (
        '/bin/bash -c "pip install --quiet --disable-pip-version-check --no-warn-script-location '
        'dlt[snowflake,filesystem,az,bigquery] psycopg2-binary pymssql sqlalchemy requests redis && python /app/pipeline.py"'
    )

    try:
        # docker.from_env() used to sit outside this try block, so a down
        # Docker daemon (very possible on Windows if Docker Desktop isn't
        # running) crashed the whole request as an unhandled exception
        # instead of being treated as one failed sandbox attempt.
        docker_client = docker.from_env()
        container = docker_client.containers.run(
            image=image_name,
            command=setup_and_run_cmd,
            volumes=volumes,
            environment=env_vars,
            extra_hosts={"host.docker.internal": "host-gateway"},
            detach=True
        )

        result = container.wait()
        logs = container.logs().decode('utf-8')
        container.remove()

        return (result['StatusCode'] == 0, logs.strip())
    except Exception as e:
        return (False, f"Sandbox runtime container exception: {str(e)}")


def heal_script(broken_code: str, error_log: str, schema_summary: dict = None) -> str:
    """Attempts code repair using Anthropic Claude first, falling back to OpenAI GPT-4o on error."""

    # default=str handles datetime/non-serializable objects cleanly
    user_prompt = json.dumps({
        "broken_code": broken_code,
        "error_traceback": error_log,
        "schema_summary": schema_summary or {}
    }, default=str)

    # Primary Attempt: Anthropic Claude Sonnet 5, via native structured outputs.
    if anthropic_client:
        try:
            print("[Self-Healer] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            response = anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=16000,
                thinking={"type": "disabled"},
                system=HEALER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=HealedPipeline,
            )
            parsed = response.parsed_output
            if parsed is None:
                raise RuntimeError(
                    f"Claude did not return a complete structured response "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})."
                )
            print(f"[Healer Diagnosis (Anthropic)]: {parsed.root_cause}")
            print(f"[Changes Applied]: {parsed.changes_made}\n")
            return parsed.fixed_code

        except Exception as e:
            print(f"[Warning] Anthropic API failed or encountered error: {e}")
            print("[Self-Healer] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    # Fallback Attempt: OpenAI GPT-4o, via its own native structured outputs.
    if openai_client:
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=HealedPipeline,
                messages=[
                    {"role": "system", "content": HEALER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ]
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(
                    response.choices[0].message.refusal
                    or "OpenAI declined to produce a structured response."
                )
            print(f"[Healer Diagnosis (OpenAI Fallback)]: {parsed.root_cause}")
            print(f"[Changes Applied]: {parsed.changes_made}\n")
            return parsed.fixed_code

        except Exception as e:
            raise RuntimeError(f"OpenAI fallback execution failed: {e}")

    raise ValueError("Neither Anthropic nor OpenAI execution succeeded.")


def execute_with_self_healing(
    script_path: str, 
    schema_summary: dict = None, 
    max_retries: int = 3,
    source_db_url: str = None,
    dest_db_url: str = None,
    extra_env: dict = None,
    needs_source_db: bool = True,
    source_files_dir: str = None,
    needs_dest_db: bool = True,
) -> tuple[bool, int, str, str]:
    """Executes script in sandbox and auto-repairs on failure up to max_retries.

    Returns (success, attempts, logs, final_code). final_code is read
    from script_path itself at the moment of return - self-healing
    rewrites that file in place on disk each time it repairs something,
    so it's the only reliable source of "what actually got tested last".
    A caller that persists its own original in-memory `code` variable
    instead of this return value silently saves the pre-heal attempt
    while the logs describe a later, different, healed version -
    exactly the gap this return value exists to close.
    """
    if schema_summary is None:
        from introspect import introspect_schema
        schema_summary = introspect_schema()

    def _read_current_code() -> str:
        with open(script_path, "r", encoding="utf-8") as f:
            return f.read()

    last_logs = ""
    for attempt in range(1, max_retries + 1):
        print(f"--- Sandbox Run (Attempt {attempt}/{max_retries}) ---")
        success, logs = run_in_sandbox(
            script_path, source_db_url, dest_db_url, extra_env, needs_source_db, source_files_dir, needs_dest_db,
        )
        last_logs = logs

        if success:
            print("\nPipeline execution succeeded!")
            print(logs)
            return True, attempt, logs, _read_current_code()

        print(f"\nExecution failed on attempt {attempt}.")
        print("--- Execution Error Traceback ---")
        print(logs)

        if attempt < max_retries:
            print(f"\nTriggering AI self-healing loop (Retry {attempt})...")
            with open(script_path, "r", encoding="utf-8") as f:
                broken_code = f.read()

            fixed_code = heal_script(broken_code, logs, schema_summary)

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(fixed_code)
            print(f"Updated {script_path} with auto-healed code. Retrying execution...\n")
        else:
            print("\nReached maximum retry threshold. Self-healing unsuccessful.")
            return False, attempt, last_logs, _read_current_code()

    return False, max_retries, last_logs, _read_current_code()


if __name__ == "__main__":
    execute_with_self_healing("generated_pipeline.py")
