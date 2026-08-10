import os
import json
import re
import docker
import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

HEALER_SYSTEM_PROMPT = """You are an expert Python data engineering agent specializing in `dlt` and database pipelines.
You are given a broken Python ETL script and the runtime error/traceback produced when executing it inside a Docker container.

Your job is to fix the code so that it executes without errors.
- Preserve the overall pipeline logic and business goal.
- Do NOT invent database credentials; keep reading from environment variables (SOURCE_DB_URL, DEST_DB_URL).
- Return ONLY valid JSON matching this exact structure with no extra text or explanations:

{
  "fixed_code": "string containing the complete corrected Python script",
  "root_cause": "brief plain text explanation of what caused the failure",
  "changes_made": "summary of fixes applied"
}
"""

def clean_json_response(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def run_in_sandbox(script_path: str) -> tuple[bool, str]:
    """Runs the specified script in an isolated Docker container.
    Returns a tuple: (success: bool, logs: str)
    """
    docker_client = docker.from_env()
    abs_path = os.path.abspath(script_path)
    image_name = "python:3.10-slim"

    env_vars = {
        "SOURCE_DB_URL": "postgresql://postgres:devpass@host.docker.internal:5433/testdb",
        "DEST_DB_URL": "postgresql://postgres:devpass@host.docker.internal:5433/testdb",
    }

    setup_and_run_cmd = (
        '/bin/bash -c "pip install --quiet --disable-pip-version-check '
        'dlt psycopg2-binary sqlalchemy && python /app/pipeline.py"'
    )

    try:
        container = docker_client.containers.run(
            image=image_name,
            command=setup_and_run_cmd,
            volumes={abs_path: {'bind': '/app/pipeline.py', 'mode': 'ro'}},
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


def heal_script(broken_code: str, error_log: str) -> str:
    """Passes broken code and execution error traceback to Claude for repair."""
    user_prompt = json.dumps({
        "broken_code": broken_code,
        "error_traceback": error_log
    })

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        system=HEALER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text_blocks = [
        block.text for block in response.content 
        if getattr(block, "type", None) == "text" or hasattr(block, "text")
    ]
    
    cleaned = clean_json_response(text_blocks[0])
    parsed = json.loads(cleaned)
    
    print(f"\n[Healer Diagnosis]: {parsed.get('root_cause', 'N/A')}")
    print(f"[Changes Applied]: {parsed.get('changes_made', 'N/A')}\n")
    
    return parsed["fixed_code"]


def execute_with_self_healing(script_path: str, max_retries: int = 3):
    """Executes a pipeline script in the sandbox and attempts auto-repair on failure up to max_retries."""
    for attempt in range(1, max_retries + 1):
        print(f"--- Sandbox Run (Attempt {attempt}/{max_retries}) ---")
        success, logs = run_in_sandbox(script_path)

        if success:
            print("\nPipeline execution succeeded!")
            print(logs)
            return True

        print(f"\nExecution failed on attempt {attempt}.")
        print("--- Execution Error Traceback ---")
        print(logs)

        if attempt < max_retries:
            print(f"\nTriggering AI self-healing loop (Retry {attempt})...")
            with open(script_path, "r", encoding="utf-8") as f:
                broken_code = f.read()

            fixed_code = heal_script(broken_code, logs)

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(fixed_code)
            print(f"Updated {script_path} with auto-healed code. Retrying execution...\n")
        else:
            print("\nReached maximum retry threshold. Self-healing unsuccessful.")
            return False


if __name__ == "__main__":
    execute_with_self_healing("generated_pipeline.py")