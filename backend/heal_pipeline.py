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


HEALER_SYSTEM_PROMPT = """You are an expert Python data engineering agent specializing in `dlt` and database pipelines.
You are given a broken Python ETL script, schema metadata of the source database, and the runtime error/traceback produced when executing it inside a Docker container.

Your job is to fix the code so that it executes without errors.
- Preserve the overall pipeline logic and business goal.
- Use actual existing tables and columns defined in the schema summary.
- Do NOT invent database credentials; keep reading from environment variables (SOURCE_DB_URL, DEST_DB_URL).
- Only call functions that genuinely exist in the `dlt` public API. If you are not certain a function exists, do not use it.
- The destination database engine (SQLite, Postgres, etc.) is not known ahead of time and must never be assumed. If the error is destination/credentials-related, the fix is always the generic SQLAlchemy destination, which auto-detects the right dialect from the connection string - never an engine-specific one like `dlt.destinations.postgres(...)`:

      import sqlalchemy as sa
      dest_engine = sa.create_engine(os.environ["DEST_DB_URL"])
      destination=dlt.destinations.sqlalchemy(dest_engine)

- Return ONLY valid JSON matching this exact structure with no extra text or explanations:

{
  "fixed_code": "string containing the complete corrected Python script",
  "root_cause": "brief plain text explanation of what caused the failure",
  "changes_made": "summary of fixes applied"
}
"""

def _resolve_sandbox_db_urls(script_dir: str, source_db_url: str = None, dest_db_url: str = None):
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

    Returns (source_url, dest_url, extra_volumes: dict)
    """
    source_url = source_db_url or os.environ.get("SOURCE_DB_URL") or get_db_url()
    dest_url = dest_db_url or os.environ.get("DEST_DB_URL") or source_url

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

    return adapt(source_url), adapt(dest_url), extra_volumes


def run_in_sandbox(script_path: str, source_db_url: str = None, dest_db_url: str = None) -> tuple[bool, str]:
    """Runs the specified script in an isolated Docker container."""
    abs_path = os.path.abspath(script_path)
    script_dir = os.path.dirname(abs_path) or "."
    image_name = "python:3.10-slim"

    source_url, dest_url, extra_volumes = _resolve_sandbox_db_urls(script_dir, source_db_url, dest_db_url)
    env_vars = {
        "SOURCE_DB_URL": source_url,
        "DEST_DB_URL": dest_url,
    }

    volumes = {abs_path: {'bind': '/app/pipeline.py', 'mode': 'ro'}}
    volumes.update(extra_volumes)

    setup_and_run_cmd = (
        '/bin/bash -c "pip install --quiet --disable-pip-version-check --no-warn-script-location '
        'dlt psycopg2-binary sqlalchemy && python /app/pipeline.py"'
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
) -> tuple[bool, int, str]:
    """Executes script in sandbox and auto-repairs on failure up to max_retries."""
    if schema_summary is None:
        from introspect import introspect_schema
        schema_summary = introspect_schema()

    last_logs = ""
    for attempt in range(1, max_retries + 1):
        print(f"--- Sandbox Run (Attempt {attempt}/{max_retries}) ---")
        success, logs = run_in_sandbox(script_path, source_db_url, dest_db_url)
        last_logs = logs

        if success:
            print("\nPipeline execution succeeded!")
            print(logs)
            return True, attempt, logs

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
            return False, attempt, last_logs

    return False, max_retries, last_logs


if __name__ == "__main__":
    execute_with_self_healing("generated_pipeline.py")
