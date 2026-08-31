"""
Stage A: natural-language goal + schema metadata -> a reviewable
architecture proposal, produced BEFORE any code is written.

This is a new, earlier checkpoint than generate_pipeline.py's code
generation - it exists specifically to catch a goal that doesn't
actually match anything in the real schema, before a single line of
code gets written against it. Three separate times this session, a
goal with nothing real to back it up (a fabricated `patient_vitals`
env var, a nonexistent `raw_orders` table, a nonexistent `orders_raw`
table) got past generation and only surfaced as a sandbox failure or
a silently-empty destination table, well after real time was spent on
it. This step is meant to catch that up front instead.

Same structured-outputs approach as generate_pipeline.py (each
provider's native support, not JSON-in-prose), and the same
max_tokens/thinking-disabled tuning that took real debugging to get
right there - reused here rather than re-guessed, since it's the same
class of call (Sonnet 5, structured output, similar-scale goal/schema
input).
"""
import json
import os
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
import anthropic
import openai

load_dotenv(override=False)

anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class ArchitectureProposal(BaseModel):
    feasible: bool
    # Required either way - if feasible, briefly why; if not, exactly
    # which part of the goal has nothing to back it up in the schema.
    feasibility_notes: str
    summary: str
    # Real table names from the provided schema only - the system
    # prompt is explicit that these can't be invented.
    source_tables_used: List[str] = Field(default_factory=list)
    destination_dataset: Optional[str] = None
    destination_table: Optional[str] = None
    approach: Optional[str] = None
    key_transformations: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a data pipeline architect. Given a database schema \
summary and a plain-English goal, assess whether the goal can actually be \
fulfilled using ONLY the tables and columns present in the schema, and \
propose a high-level approach - not code, not yet.

This is a review checkpoint that happens BEFORE any code gets written, \
specifically to catch goals that don't match the real data available. Be \
honest and direct about feasibility - do not invent tables, staging areas, \
external data sources, or environment variables that "will contain the \
data" to make an infeasible goal look achievable. If the schema doesn't \
have what the goal describes, say so plainly in feasibility_notes and set \
feasible to false. A pipeline that reads real data from the wrong table, or \
one that reads nothing at all from a fabricated table, is a worse outcome \
than telling the reviewer up front that the goal doesn't match what's \
actually available.

Set feasible to true only if every table you plan to read from is a real \
table name present in the provided schema_summary. Partial feasibility (the \
goal is mostly achievable but one piece isn't) should still be \
feasible=false, with feasibility_notes explaining exactly which part can't \
be fulfilled and why - do not round a partial match up to a full yes.

If feasible, describe the intended approach in plain language: which real \
tables you'll read (source_tables_used - actual names from schema_summary, \
never names you're introducing), what transformation logic will happen \
(key_transformations), and where the result will be written \
(destination_dataset and destination_table).

Do not write any code. This is a plan for a human to review before code \
generation begins."""


def propose_architecture(schema_summary: dict, goal: str) -> dict:
    user_content = json.dumps({"schema_summary": schema_summary, "goal": goal}, default=str)

    if anthropic_client:
        try:
            print("[Architect] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            response = anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=16000,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=ArchitectureProposal,
            )
            if response.parsed_output is None:
                raise RuntimeError(
                    f"Claude did not return a complete structured response "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})."
                )
            return response.parsed_output.model_dump()
        except Exception as e:
            print(f"[Warning] Anthropic architecture proposal failed: {e}")
            print("[Architect] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    if openai_client:
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=ArchitectureProposal,
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
            raise RuntimeError(f"OpenAI fallback architecture proposal failed: {e}")

    raise ValueError("No AI provider is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")


if __name__ == "__main__":
    from introspect import introspect_schema

    print("Introspecting schema...")
    schema = introspect_schema()

    goal = input("Describe the pipeline goal (plain English): ")
    print("\nAssessing feasibility and proposing an architecture...\n")

    result = propose_architecture(schema, goal)

    print(f"Feasible: {result['feasible']}")
    print(f"Notes: {result['feasibility_notes']}")
    print(f"\nSummary: {result['summary']}")
    if result.get("source_tables_used"):
        print(f"Source tables: {', '.join(result['source_tables_used'])}")
    if result.get("destination_table"):
        print(f"Destination: {result.get('destination_dataset')}.{result.get('destination_table')}")
