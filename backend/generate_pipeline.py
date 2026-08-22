"""
Phase 1: natural-language goal + schema metadata -> generated pipeline code.

The AI receives only the schema summary produced by introspect.py and the
user's plain-English goal. It never sees, and is explicitly told not to
invent, connection credentials — those are injected at run time by whatever
executes the generated script, not by the AI.
"""
import os
import json
import re
from dotenv import load_dotenv
import anthropic
import openai

load_dotenv(override=False)

# Initialize clients if environment variables exist
anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None

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

Return ONLY valid JSON matching this exact shape, no other text, no \
markdown fences:

{
  "pipeline_name": "string",
  "description": "string",
  "code": "string containing the full Python script",
  "assumptions": ["string", ...]
}
"""


def clean_json_response(raw_text: str) -> str:
    """Strips Markdown code fences and extracts raw JSON content."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def generate_pipeline(schema_summary: dict, goal: str) -> dict:
    # default=str converts datetime/date objects into standard strings during serialization
    user_content = json.dumps({"schema_summary": schema_summary, "goal": goal}, default=str)

    # Primary Attempt: Anthropic Claude Sonnet 5
    if anthropic_client:
        try:
            print("[Generator] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            response = anthropic_client.messages.create(
                model="claude-sonnet-5",
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )

            # Filter content blocks to extract text and ignore ThinkingBlock objects
            text_blocks = [
                block.text for block in response.content 
                if getattr(block, "type", None) == "text" or hasattr(block, "text")
            ]
            if text_blocks:
                cleaned_text = clean_json_response(text_blocks[0])
                return json.loads(cleaned_text)

        except Exception as e:
            print(f"[Warning] Anthropic pipeline generation failed: {e}")
            print("[Generator] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    # Fallback Attempt: OpenAI GPT-4o
    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ]
            )

            cleaned_text = clean_json_response(response.choices[0].message.content)
            return json.loads(cleaned_text)

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
