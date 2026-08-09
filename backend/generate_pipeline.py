"""
Phase 1: natural-language goal + schema metadata -> generated pipeline code.

The AI receives only the schema summary produced by introspect.py and the
user's plain-English goal. It never sees, and is explicitly told not to
invent, connection credentials — those are injected at run time by whatever
executes the generated script, not by the AI.
"""
import os
import json
from dotenv import load_dotenv
import anthropic

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a data pipeline generator. Given a database schema \
summary and a plain-English goal, generate a Python ETL script using the \
`dlt` library (https://dlthub.com) that accomplishes the goal.

Connection details (host, user, password, etc.) will be injected as \
environment variables at run time by the calling system. Never invent, \
guess, or reference specific credential values — read them from os.environ \
using generic names like SOURCE_DB_URL / DEST_DB_URL.

Return ONLY valid JSON matching this exact shape, no other text, no \
markdown fences:

{
  "pipeline_name": "string",
  "description": "string",
  "code": "string containing the full Python script",
  "assumptions": ["string", ...]
}
"""


def generate_pipeline(schema_summary: dict, goal: str) -> dict:
    user_content = json.dumps({"schema_summary": schema_summary, "goal": goal})

    response = client.messages.create(
        model="claude-sonnet-5",  # check console.anthropic.com for the current default
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw_text = response.content[0].text
    return json.loads(raw_text)


if __name__ == "__main__":
    from introspect import introspect_schema

    print("Introspecting schema...")
    schema = introspect_schema()

    goal = input("Describe the pipeline you want (plain English): ")
    print("\nGenerating pipeline...\n")

    result = generate_pipeline(schema, goal)

    print(f"--- {result['pipeline_name']} ---")
    print(result["description"])
    if result.get("assumptions"):
        print("\nAssumptions made:")
        for a in result["assumptions"]:
            print(f"  - {a}")

    out_path = "generated_pipeline.py"
    with open(out_path, "w") as f:
        f.write(result["code"])
    print(f"\nSaved to {out_path} — review before running against real data.")
