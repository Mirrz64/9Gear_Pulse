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

Stage B addition: the proposal now names actual files in build order,
not just a prose approach. Each file's reads_from must be either a
real schema table or an EARLIER file's destination_table in this same
plan - never a later one. That's what makes the plan buildable
sequentially: file 2 can only depend on a table file 1 will have
actually materialized by the time file 2 is generated and tested,
never on something that doesn't exist yet.

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
from ai_provider import call_with_rate_limit_backoff
import openai

load_dotenv(override=False)

anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class ProposedFile(BaseModel):
    file_name: str
    purpose: str
    # Real schema table names and/or earlier files' destination_table
    # values from this SAME files list - never a later file's, and
    # never a table not present in the schema or in an earlier file.
    reads_from: List[str] = Field(default_factory=list)
    destination_table: str
    # Where this file conceptually sits in the exported project tree -
    # null/empty for the project root, otherwise a folder name (e.g.
    # "src"). Purely a planning/display hint for project_structure's
    # tree - file_name itself stays a bare filename throughout, since
    # generation, the sandbox, and PipelineVersionFile.file_name all
    # depend on that never changing.
    directory: Optional[str] = None


class ScaffoldFile(BaseModel):
    """A supporting project file beyond the pipeline .py files already
    covered by ArchitectureProposal.files - infra/config a project
    needs to be genuinely runnable standalone (outside 9Gear Pulse's
    own sandbox, which every pipeline file already assumes exists),
    or documentation proposed for planning visibility only.
    """
    path: str
    purpose: str
    # A closed-ish vocabulary the generation step downstream can
    # dispatch on directly, rather than re-parsing free-text purpose
    # to guess what kind of file this is: "readme", "license",
    # "docker_compose", "env_example", "gitignore", "requirements",
    # "airflow_dag", "other".
    scaffold_type: str
    # False only for readme/license - their real content is prose/
    # legal text this system has no genuine basis to author, so
    # they're proposed for planning visibility only and never
    # actually generated. Everything else with generated=true is a
    # real file a later step will produce.
    generated: bool


class ArchitectureProposal(BaseModel):
    feasible: bool
    # Required either way - if feasible, briefly why; if not, exactly
    # which part of the goal has nothing to back it up in the schema.
    feasibility_notes: str
    summary: str
    # Real table names from the provided schema only - the system
    # prompt is explicit that these can't be invented.
    source_tables_used: List[str] = Field(default_factory=list)
    # The FINAL output of the whole pipeline - matches the last file's
    # destination_table when files is populated.
    destination_dataset: Optional[str] = None
    destination_table: Optional[str] = None
    approach: Optional[str] = None
    key_transformations: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    # The actual build plan, in the order files must be generated,
    # tested, and reviewed. Empty when feasible is false - there's
    # nothing to plan around a goal that doesn't match the schema.
    files: List[ProposedFile] = Field(default_factory=list)
    # A Mermaid flowchart describing the same data flow as `files`
    # above, visually - empty when feasible is false, same as files.
    architecture_diagram_mermaid: Optional[str] = None
    # Supporting project structure beyond the pipeline files already
    # in `files` - see ScaffoldFile's own docstring for what belongs
    # here and what doesn't.
    project_structure: List[ScaffoldFile] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a data pipeline architect. Given a database schema \
summary and a plain-English goal, assess whether the goal can actually be \
fulfilled using ONLY the tables and columns present in the schema, and \
propose a high-level approach and a concrete, file-by-file build plan - not \
code, not yet.

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

A schema_summary entry with "schema": "redis" describes a Redis KEY \
PATTERN, not a relational table - its "table" value (e.g. "user:*") is a \
SCAN-matchable pattern covering many real keys, not one literal key. Treat \
it exactly like any other real table name for source_tables_used and \
reads_from purposes - the same "only real names already in schema_summary, \
never one you're introducing" rule applies identically. Two extra fields \
appear only on these entries and exist specifically to inform the build \
plan: redis_type (the actual Redis data structure - string, hash, list, \
set, zset, or stream - which determines what reading a matching key \
actually looks like) and example_key (one real key that matched the \
pattern during introspection, showing the real naming convention in use). \
A file whose reads_from includes a Redis pattern must mention in its \
purpose which redis_type it's reading, since that directly determines how \
the file's code will need to read each key.

Up to four more fields may appear in the input beyond schema_summary and \
goal, each present only when the project's creator actually filled it in - \
never assume any of them are there, and never treat their absence as \
missing information to ask about:

- objectives, when present, elaborates on WHY this project exists and what \
a successful outcome looks like, beyond the bare goal statement. Treat it \
as extending the goal itself, not as a separate or competing requirement -\
 fold it into your understanding of what's actually being asked for.
- dataset_notes, when present, explains what raw values in the schema \
actually MEAN - things the schema's structure alone can't convey, like a \
status column's codes mapping to specific business states, or a field's \
real-world units or conventions. Reflect this directly in key_transformations \
and approach: if dataset_notes says a code means something specific, your \
proposed transformation should say so too, not leave it as an opaque \
pass-through.
- known_constraints, when present, describes operational realities to \
design around - things like an API's rate limit, expected duplicate \
records needing a dedup step, or a known data quality issue in the source. \
Reflect these in assumptions, and in the file-by-file build plan itself \
where they genuinely change what needs building (e.g. a known-duplicates \
constraint may mean the plan needs a step that dedupes by a stated key \
before writing the final destination).
- load_pattern, when present, is the project creator's stated preference \
for how the destination table should be maintained: "full_refresh" (replace \
the destination entirely each run), "append_only" (only ever add new \
records, never modify existing ones), or "incremental" (update changed \
records and add new ones, based on a key - an upsert/merge). State which \
pattern the approach follows explicitly, and when it's "incremental", the \
build plan should make clear what key or watermark column the merge is \
based on, if the schema makes that determinable.

If feasible, describe the intended approach in plain language: which real \
tables you'll read (source_tables_used - actual names from schema_summary, \
never names you're introducing), what transformation logic will happen \
(key_transformations), and where the final result will be written \
(destination_dataset and destination_table - destination_table here \
follows the same "schema.table" rule detailed below for each file's own \
destination_table, never a bare table name alone).

Then break the work into files, listed in the exact order they must be \
built. Each file writes to its own real, named destination_table, ALWAYS \
written as "schema.table" with an explicit schema prefix - never a bare \
table name alone, even when only one schema is realistically in play. \
This is a hard rule, not just what the example below happens to show: a \
bare table name here leaves the actual destination schema ambiguous to \
whatever generates and re-generates this file's code later, and \
different attempts can and do pick different schemas for the exact same \
bare name when nothing pins it down. This plan will be built and tested \
one file at a time, each file's destination \
table verified to genuinely exist before the next file is allowed to \
depend on it. Because of that, a file's reads_from list may only contain: \
real table names from schema_summary, or the destination_table of an \
EARLIER file in this same files list. Never a later file's table, and \
never a table you're introducing that isn't backed by the schema or an \
earlier file's real output.

Most goals need only one file - do not split a simple, single-stage \
transformation into multiple files for its own sake. Use more than one \
file only when there's a genuine staging reason to (for example, a \
distinct cleansing/masking step whose output several later steps will \
each depend on, or a bronze/silver/gold pattern the goal actually calls \
for). When in doubt, prefer fewer files.

Two more things to produce alongside the file-by-file build plan, only \
when feasible is true:

Architecture Diagram — architecture_diagram_mermaid must be a complete, \
syntactically valid Mermaid flowchart (`flowchart LR` or `graph LR`) \
showing the real data flow: the actual source table(s), through each \
file in the build plan in order, to the final destination. Label edges \
with what actually happens ("reads", "writes", "parses") rather than \
leaving them bare. Use the exact same names already used elsewhere in \
this response (source_tables_used, each file's file_name and \
destination_table) so the diagram matches the prose exactly instead of \
introducing new names for the same things. Example shape:

    flowchart LR
        A[bronze.bronze_events] -->|reads| B[01_parse_events.py]
        B -->|writes| C[silver.stg_events]
        C -->|reads| D[02_silver_customers.py]
        D -->|writes| E[silver.silver_customers]

Project Structure — project_structure lists the supporting files this \
project has beyond the pipeline files already in `files`. Four entries \
are ALWAYS present: README.md ("Project overview and usage \
instructions", scaffold_type "readme", generated=false, since its real \
content is prose this system has no genuine basis to author), LICENSE \
("Project license", scaffold_type "license", generated=false, same \
reasoning), requirements.txt ("Python dependencies", scaffold_type \
"requirements", generated=true), and .gitignore ("Files and folders \
git should not track", scaffold_type "gitignore", generated=true) - \
unlike docker-compose.yml or an Airflow DAG, every exported pipeline \
needs its dependencies listed and a sane .gitignore to actually be \
runnable standalone, regardless of what that specific project's own \
infrastructure needs, so these two are never conditional. Beyond those \
four, only propose an additional infra/config file when THIS project's \
own execution genuinely needs something not already provided — \
9Gear Pulse's own sandbox and destination database are already \
provisioned for every pipeline, so do not propose docker-compose.yml \
or .env merely because a standalone data engineering project typically \
has one; only propose them if this pipeline needs something genuinely \
beyond that. The same restraint applies to an Airflow DAG — only \
propose one when the build plan's own scheduling/dependency needs are \
complex enough to actually warrant it (e.g. multiple independent \
schedules or non-trivial cross-pipeline dependencies); a simple daily \
or hourly run, which 9Gear Pulse's own scheduler already handles, is \
not a reason to add one. When in doubt about anything beyond the four \
always-present files, propose nothing more — the same "prefer fewer" \
restraint that governs the file-by-file build plan above applies here \
too. Valid scaffold_type values beyond "readme"/"license": \
"docker_compose", "env_example", "gitignore", "requirements", \
"airflow_dag", "other".

Where things live in the tree — project_structure's `path` may include \
a folder prefix (e.g. "dags/pipeline_dag.py") when a file genuinely \
belongs in its own subfolder rather than the project root. An Airflow \
DAG in particular must never sit directly in the root next to README - \
give it its own folder (e.g. "dags/"). README.md, LICENSE, \
requirements.txt, and .gitignore are conventionally root-level (a bare \
filename, no folder prefix) unless there's a real reason otherwise. \
Separately, each entry in `files` (the pipeline build plan) has its \
own `directory` field for the SAME purpose - null/empty for the \
project root, or a folder name (e.g. "src") if this project's files \
belong together in one. A small build plan (a file or two) sitting \
directly in the root is completely normal - don't invent a subfolder \
for its own sake; use one when it genuinely groups related files \
together or matches what the rest of project_structure already implies \
(e.g. pipeline files alongside a "dags/" folder for their own \
orchestration).

Do not write any code. This is a plan for a human to review before code \
generation begins."""


def propose_architecture(
    schema_summary: dict,
    goal: str,
    objectives: Optional[str] = None,
    dataset_notes: Optional[str] = None,
    known_constraints: Optional[str] = None,
    load_pattern: Optional[str] = None,
) -> dict:
    payload = {"schema_summary": schema_summary, "goal": goal}
    # Each included only when actually set - a project created before
    # these fields existed, or one where a user skipped them (they're
    # genuinely optional), sends exactly the same payload this
    # function has always sent, rather than four empty/null fields
    # cluttering every request.
    if objectives:
        payload["objectives"] = objectives
    if dataset_notes:
        payload["dataset_notes"] = dataset_notes
    if known_constraints:
        payload["known_constraints"] = known_constraints
    if load_pattern:
        payload["load_pattern"] = load_pattern
    user_content = json.dumps(payload, default=str)

    if anthropic_client:
        try:
            print("[Architect] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            response = call_with_rate_limit_backoff(lambda: anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=16000,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=ArchitectureProposal,
            ))
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
    objectives = input("Objectives (optional, press enter to skip): ").strip() or None
    dataset_notes = input("Dataset notes (optional, press enter to skip): ").strip() or None
    known_constraints = input("Known constraints (optional, press enter to skip): ").strip() or None
    load_pattern = input("Load pattern - full_refresh/append_only/incremental (optional, press enter to skip): ").strip() or None
    print("\nAssessing feasibility and proposing an architecture...\n")

    result = propose_architecture(schema, goal, objectives, dataset_notes, known_constraints, load_pattern)

    print(f"Feasible: {result['feasible']}")
    print(f"Notes: {result['feasibility_notes']}")
    print(f"\nSummary: {result['summary']}")
    if result.get("source_tables_used"):
        print(f"Source tables: {', '.join(result['source_tables_used'])}")
    if result.get("files"):
        print("\nBuild plan:")
        for f in result["files"]:
            reads = ", ".join(f["reads_from"]) or "(none)"
            print(f"  {f['file_name']}: reads [{reads}] -> writes {f['destination_table']}")
    elif result.get("destination_table"):
        print(f"Destination: {result.get('destination_dataset')}.{result.get('destination_table')}")

    if result.get("architecture_diagram_mermaid"):
        print("\nArchitecture diagram (Mermaid):")
        print(result["architecture_diagram_mermaid"])

    if result.get("project_structure"):
        print("\nProject structure:")
        for sf in result["project_structure"]:
            marker = "[generated]" if sf["generated"] else "[planning only]"
            print(f"  {sf['path']} {marker} - {sf['purpose']}")
