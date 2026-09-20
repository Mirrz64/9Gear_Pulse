"""
Stage C: an approved scaffold-file plan entry (path, purpose,
scaffold_type) -> real generated file content.

Distinct from generate_pipeline.py: a scaffold file isn't a dlt
pipeline read against a schema - it's a docker-compose.yml, .gitignore,
requirements.txt, .env.example, or Airflow DAG describing how the
ALREADY-approved pipeline build plan runs standalone, outside 9Gear
Pulse's own sandbox. The system prompt varies meaningfully by
scaffold_type, since a YAML compose file and a Python DAG have nothing
in common structurally - dispatched from one shared entry point rather
than duplicating the whole structured-output/fallback-provider
plumbing per type.

Same structured-outputs approach as generate_pipeline.py and
propose_architecture.py (each provider's native support), and the same
thinking-disabled tuning already proven right for this call shape.
"""
import json
import os
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field
import anthropic
from ai_provider import call_with_rate_limit_backoff
import openai

load_dotenv(override=False)

anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class GeneratedScaffoldFile(BaseModel):
    content: str
    assumptions: List[str] = Field(default_factory=list)


# Keyed by ScaffoldFileType's values in models.py - keep the two in
# sync if a new type is ever added there.
TYPE_GUIDANCE = {
    "docker_compose": """\
Generate a docker-compose.yml that lets the approved pipeline files \
run standalone, outside 9Gear Pulse's own infrastructure. Provision \
only what the pipeline files' own code actually needs (e.g. a \
Postgres service matching the destination they write to) - never a \
service the pipeline doesn't use. Reference credentials only as \
environment variables the accompanying .env.example would define \
(e.g. ${POSTGRES_PASSWORD}) - never a literal password or real \
connection string anywhere in this file.""",
    "env_example": """\
Generate a .env.example listing every environment variable the \
approved pipeline files actually reference (e.g. SOURCE_DB_URL, \
DEST_DB_URL, SOURCE_API_AUTH_HEADERS) with placeholder, non-real \
values - never a real credential, connection string, or secret \
anywhere in this file.""",
    "gitignore": """\
Generate a .gitignore appropriate for a Python data-pipeline project: \
virtual environments, __pycache__, .env (never .env.example, which \
is meant to be committed), IDE folders, and anything else standard \
for this kind of project.""",
    "requirements": """\
Generate a requirements.txt listing exactly the third-party packages \
the approved pipeline files actually import (e.g. dlt, sqlalchemy, \
psycopg2-binary, requests) - never a package the code doesn't use, \
and never a version pin unless the code's own behavior genuinely \
depends on one.""",
    "airflow_dag": """\
Generate an Airflow DAG (plain Python, PythonOperator or the TaskFlow \
API) that runs the approved pipeline files in their build-plan order, \
each file as its own task, with upstream/downstream dependencies \
matching which file reads from which earlier file's destination_table \
- never invent a scheduling requirement beyond what the build plan's \
own dependencies actually call for.""",
    "other": """\
Generate this file's content based strictly on its stated purpose and \
the approved pipeline build plan below - do not introduce anything \
the purpose doesn't describe.""",
}


def generate_scaffold_file(scaffold_type: str, path: str, purpose: str, build_plan_files: list) -> dict:
    guidance = TYPE_GUIDANCE.get(scaffold_type, TYPE_GUIDANCE["other"])
    system_prompt = f"""You are generating ONE supporting project file for an \
already-approved data pipeline build plan - not pipeline code, a real \
project-scaffold file a human will actually use. Never invent \
credentials, secrets, or real connection strings anywhere in this file \
- placeholders only, always.

File: {path}
Stated purpose: {purpose}

{guidance}

Provide the complete file content and a list of any assumptions you \
made. The user message contains the approved build plan's files (name, \
purpose, reads_from, destination_table) for context - reference their \
real names/paths where relevant, never invent files that aren't in \
that list."""

    user_content = json.dumps({"build_plan_files": build_plan_files}, default=str)

    if anthropic_client:
        try:
            print(f"[Scaffold Generator] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5) for {path}...")
            response = call_with_rate_limit_backoff(lambda: anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=8000,
                thinking={"type": "disabled"},
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                output_format=GeneratedScaffoldFile,
            ))
            if response.parsed_output is None:
                raise RuntimeError(
                    f"Claude did not return complete structured output for {path} "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})."
                )
            return response.parsed_output.model_dump()
        except Exception as e:
            print(f"[Warning] Anthropic scaffold generation failed for {path}: {e}")
            print("[Scaffold Generator] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    if openai_client:
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=GeneratedScaffoldFile,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ]
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(
                    response.choices[0].message.refusal
                    or f"OpenAI declined to produce structured output for {path}."
                )
            return parsed.model_dump()
        except Exception as e:
            raise RuntimeError(f"OpenAI fallback scaffold generation failed for {path}: {e}")

    raise ValueError("No AI provider is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")
