"""
Phase 1: natural-language goal + schema metadata -> generated pipeline code.

The AI receives only the schema summary produced by introspect.py and the
user's plain-English goal. It never sees, and is explicitly told not to
invent, connection credentials — those are injected at run time by whatever
executes the generated script, not by the AI.

Uses each provider's native structured outputs (Claude's via
client.messages.parse(), OpenAI's via client.beta.chat.completions.parse())
instead of asking for JSON in prose and hoping. This closes the exact
failure class hit earlier tonight - "Unterminated string starting at..."
JSON parse errors from a response with a stray markdown fence or a
truncated field. The schema is now enforced by the provider itself via
constrained decoding, not recovered after the fact by regex-stripping
markdown fences and calling json.loads() on whatever's left.

Requires a reasonably current SDK version - run
`pip install --upgrade anthropic openai` if `.messages.parse()` /
`.beta.chat.completions.parse()` raise an AttributeError, since this
feature is newer than what `pip install -r requirements.txt` may have
installed weeks ago even with the same version floor in requirements.txt.
"""
import json
import os
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field
import anthropic
import openai

load_dotenv(override=False)

# Initialize clients if environment variables exist
anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class GeneratedPipeline(BaseModel):
    pipeline_name: str
    description: str
    code: str
    assumptions: List[str] = Field(default_factory=list)
    # What the generated code actually writes to - reported by the AI
    # itself since it's the one deciding this, rather than us trying to
    # parse it back out of the code afterward. Lets the backend run real
    # data-quality checks against the exact table without guessing.
    destination_dataset: str
    destination_table: str


SYSTEM_PROMPT = """You are a data pipeline generator. Given a database schema \
summary and a plain-English goal, generate a Python ETL script using the \
`dlt` library (https://dlthub.com) that accomplishes the goal.

Connection details (host, user, password, etc.) will be injected as \
environment variables at run time by the calling system. Never invent, \
guess, or reference specific credential values — read them from os.environ \
using generic names like SOURCE_DB_URL / DEST_DB_URL.

Destination Requirements — this is mandatory, do not deviate:
- The actual destination database engine (SQLite, Postgres, or otherwise) is \
not known to you and can vary at runtime. NEVER use an engine-specific \
destination like `dlt.destinations.postgres(...)`, `dlt.destinations.mysql(...)`, \
etc. — guessing the wrong one is a common and completely avoidable failure.
- Always use the generic SQLAlchemy destination instead, which auto-detects \
the correct dialect from the connection string itself and works identically \
across SQLite, Postgres, and other SQL engines. Set it up exactly like this:

    import sqlalchemy as sa
    dest_engine = sa.create_engine(os.environ["DEST_DB_URL"])
    pipeline = dlt.pipeline(
        pipeline_name="...",
        destination=dlt.destinations.sqlalchemy(dest_engine),
        dataset_name="...",
    )

- Never call helper functions that do not exist in the `dlt` public API (for \
example, there is no `dlt.postgres.credentials_parse`). Pass either a raw \
connection string or an actual `sqlalchemy.Engine` object, as shown above — \
nothing else.

Schema Drift Requirements:
- Configure schema contracts explicitly on the pipeline or resources to handle schema evolution cleanly.
- Ensure new columns or structural variations are safely evolved without throwing runtime schema exceptions (e.g., using schema_contract={"tables": "evolve", "columns": "evolve"}).

Provide the pipeline_name, a short description, the complete script as code, \
a list of any assumptions you made, and the exact destination_dataset and \
destination_table your code actually writes to (matching the dataset_name \
passed to dlt.pipeline() and the table_name/resource name your code loads \
into) - these are used afterward to run real data-quality checks against \
that exact table, so they must be accurate, not placeholders.
"""


def generate_pipeline(schema_summary: dict, goal: str) -> dict:
    # default=str converts datetime/date objects into standard strings during serialization
    user_content = json.dumps({"schema_summary": schema_summary, "goal": goal}, default=str)

    # Primary Attempt: Anthropic Claude Sonnet 5, via native structured outputs.
    if anthropic_client:
        try:
            print("[Generator] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            # 8000 wasn't enough (stop_reason=max_tokens on a demanding
            # goal); 32000 was enough content-wise but tripped the SDK's
            # own "streaming required for long requests" guard - a
            # heuristic based purely on the max_tokens number, not the
            # actual response size. The real culprit is that max_tokens
            # covers thinking + response combined, and Sonnet 5's default
            # adaptive thinking makes that combined size unpredictable.
            # Disabling thinking removes the unpredictable half of the
            # budget entirely - the remaining max_tokens goes purely to
            # the structured JSON response, so a modest, fixed number is
            # both safely under the streaming threshold and still
            # generous for the response content alone.
            response = anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=16000,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=GeneratedPipeline,
            )
            if response.parsed_output is None:
                # Structured outputs correctly refuses to return a result that
                # doesn't fully match the schema - most likely cause is the
                # response got cut off before finishing (a demanding goal,
                # or a long generated script, needing more than max_tokens).
                # stop_reason tells us which, rather than leaving this as an
                # opaque 'NoneType has no attribute model_dump' crash.
                raise RuntimeError(
                    f"Claude did not return a complete structured response "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')}). "
                    f"The goal may be too complex for the current token budget."
                )
            return response.parsed_output.model_dump()

        except Exception as e:
            print(f"[Warning] Anthropic pipeline generation failed: {e}")
            print("[Generator] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    # Fallback Attempt: OpenAI GPT-4o, via its own native structured outputs.
    if openai_client:
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=GeneratedPipeline,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ]
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(
                    response.choices[0].message.refusal
                    or "OpenAI declined to produce a structured response."
                )
            return parsed.model_dump()

        except Exception as e:
            raise RuntimeError(f"OpenAI fallback pipeline generation failed: {e}")

    raise ValueError("No AI provider is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")


if __name__ == "__main__":
    from introspect import introspect_schema

    print("Introspecting schema...")
    schema = introspect_schema()

    goal = input("Describe the pipeline you want (plain English): ")
    print("\nGenerating pipeline...\n")

    result = generate_pipeline(schema, goal)

    print(f"--- {result.get('pipeline_name', 'Generated Pipeline')} ---")
    print(result.get("description", "No description provided."))

    if result.get("assumptions"):
        print("\nAssumptions made:")
        for a in result["assumptions"]:
            print(f"  - {a}")

    out_path = "generated_pipeline.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result["code"])

    print(f"\nSaved to {out_path} — review before running against real data.")
