"""Proposes a destination table's schema (columns, primary key, foreign
keys, check constraints, indexes) as its own focused artifact - reviewed
and approved BEFORE any transformation code for that file is generated.

This is the "pinned schema" feature: once a file's schema is approved
here, it gets actually applied to the real destination (see
approve_file_schema in review_api.py), and generate_pipeline.py is then
told the table already exists with exactly this structure and must
conform to it - dlt's schema_contract={"columns": "freeze"} makes that
a real, enforced constraint, not just a prompt instruction the AI could
drift from on a later regeneration. That's the actual fix for the DDL
churn this whole feature exists to prevent - the schema simply isn't
something a code regeneration can silently redefine anymore, because it
was never generated as PART of the code in the first place.

Deliberately produces STRUCTURED output (columns/constraints/indexes as
real Pydantic fields), not raw SQL text the AI writes directly - the
same reason every other structural piece of this codebase avoids
trusting free-form AI-written SQL verbatim. The actual CREATE TABLE
text is rendered deterministically from this structure by
render_ddl(), in Python, following this project's own established
naming conventions (pk_<table>, chk_<table>_<column>,
idx_<table>_<column>) rather than whatever naming the AI might
otherwise invent inconsistently across different files.
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


class ProposedColumn(BaseModel):
    name: str
    # A real Postgres type name (text, bigint, double precision,
    # boolean, timestamp with time zone, date, jsonb, etc.) - not a
    # generic/abstract type the AI invents, since this gets used
    # verbatim in the rendered DDL.
    type: str
    nullable: bool = True
    primary_key: bool = False


class ProposedForeignKey(BaseModel):
    column: str
    # schema.table - a real source table from schema_summary, or an
    # earlier already-approved file's own destination_table in this
    # same build plan. Never a table that doesn't genuinely exist yet.
    references_table: str
    references_column: str


class ProposedCheckConstraint(BaseModel):
    column: str
    # Just the boolean expression body, e.g. "age >= 0 AND age <= 120"
    # or "gender = ANY (ARRAY['M', 'F', 'O'])" - render_ddl() wraps it
    # in CHECK (...) and names the constraint itself.
    expression: str


class ProposedIndex(BaseModel):
    columns: List[str]
    unique: bool = False


class ProposedFileSchema(BaseModel):
    columns: List[ProposedColumn]
    primary_key_columns: List[str] = Field(default_factory=list)
    foreign_keys: List[ProposedForeignKey] = Field(default_factory=list)
    check_constraints: List[ProposedCheckConstraint] = Field(default_factory=list)
    indexes: List[ProposedIndex] = Field(default_factory=list)
    # A short, human-readable explanation of the real design choices
    # made (why this primary key, why these constraints) - shown
    # directly in the review UI alongside the rendered DDL, so a human
    # reviewer sees the REASONING, not just the SQL.
    rationale: str


SYSTEM_PROMPT = """\
You design PostgreSQL destination table schemas for a single file within \
an approved, multi-file data pipeline build plan. You do NOT write any \
transformation code or dlt logic here - only the destination table's \
real structure: columns, types, primary key, foreign keys, check \
constraints, and indexes. This schema will be reviewed by a human, then \
actually created in the real database with CREATE TABLE, BEFORE any \
code that writes to it is ever generated - get the structure right now, \
since a later code-generation step will be constrained to conform to \
exactly what you propose here, not free to redefine it.

You will receive: the source schema_summary (real tables/columns \
available to read from - the exact same shape used throughout this \
project for schema introspection), this specific file's purpose, \
reads_from (the real tables/earlier files this file reads), \
destination_table (the real, already-decided schema-qualified table \
name, e.g. "silver.silver_customers" - never invent a different name), \
and earlier_file_schemas (a dict of {destination_table: DDL text} for \
any EARLIER files in this same build plan whose own schema has already \
been approved and actually applied to the real database - these are \
REAL, EXISTING tables, valid foreign-key targets, genuinely different \
from schema_summary's own read-only source tables).

Column design:
- Every column name and type must be grounded in what the source data \
actually contains (per schema_summary) and what the file's purpose \
says it needs to produce - never invent a column with no real basis in \
either.
- Use real, standard PostgreSQL types: text, bigint, integer, double \
precision, boolean, date, timestamp with time zone, jsonb. Prefer \
timestamp with time zone for any timestamp - never a bare, timezone-less \
timestamp for anything that could cross timezones.
- nullable should reflect genuine real-world nullability of the source \
data, not default to true out of caution - if a column is a required \
identifier, mark it NOT NULL.

Primary key: every table needs one. A single natural key (e.g. a real \
customer_id already unique in the source) is preferable to a synthetic \
one when the source genuinely has one; primary_key_columns may list more \
than one column for a genuine composite key (e.g. a fact table keyed on \
customer_id + event_time).

Foreign keys: only propose one when the referenced table is REAL and \
ALREADY EXISTS - either a real table in schema_summary, or an EARLIER \
file's own destination_table as given in earlier_file_schemas (never a \
later file's, never a table this same file is itself creating, never a \
table that doesn't appear anywhere in the provided context).

Check constraints: propose one only for a genuine, real business rule \
evident from the source data or the file's purpose - a bounded numeric \
range (age, income, quantity), an enumerated set of real values a \
text/categorical column actually takes (e.g. gender values actually \
seen in the source), not a hypothetical rule with no basis in what's \
actually there.

Indexes: propose one for a column genuinely likely to be filtered or \
joined on later - a foreign key column, a natural lookup key, a \
timestamp column a fact table would commonly be range-queried by. Do \
not index every column; an index has a real storage and write cost, \
propose only ones with a real, stated reason.

rationale: 2-4 sentences explaining the real reasoning behind the \
primary key choice and any constraints/indexes proposed - written for \
a human reviewer deciding whether to approve this schema, not for \
yourself.
"""


def render_ddl(destination_table: str, schema: ProposedFileSchema) -> str:
    """Deterministically renders a real CREATE TABLE statement from a
    ProposedFileSchema - never the AI's own raw SQL text. Constraint
    and index names follow this project's own established convention,
    visible throughout its earlier real DDL work: pk_<table>,
    fk_<table>_<column>, chk_<table>_<column>, idx_<table>_<column> -
    <table> here is always the bare table name (the part after the
    schema prefix), matching how every other part of this codebase
    already derives a bare name from a schema-qualified
    destination_table (e.g. generation_tasks.py's own
    pipeline_name derivation).
    """
    db_schema, bare_table = (
        destination_table.split(".", 1) if "." in destination_table else ("public", destination_table)
    )

    column_lines = []
    for col in schema.columns:
        line = f'    "{col.name}" {col.type}'
        if not col.nullable:
            line += " NOT NULL"
        column_lines.append(line)

    constraint_lines = []
    if schema.primary_key_columns:
        pk_cols = ", ".join(f'"{c}"' for c in schema.primary_key_columns)
        constraint_lines.append(f'    CONSTRAINT pk_{bare_table} PRIMARY KEY ({pk_cols})')

    for fk in schema.foreign_keys:
        constraint_lines.append(
            f'    CONSTRAINT fk_{bare_table}_{fk.column} FOREIGN KEY ("{fk.column}") '
            f'REFERENCES {fk.references_table} ("{fk.references_column}")'
        )

    for chk in schema.check_constraints:
        constraint_lines.append(
            f'    CONSTRAINT chk_{bare_table}_{chk.column} CHECK ({chk.expression})'
        )

    all_lines = column_lines + constraint_lines
    body = ",\n".join(all_lines)

    ddl = f'CREATE TABLE IF NOT EXISTS "{db_schema}"."{bare_table}" (\n{body}\n);'

    for idx in schema.indexes:
        idx_cols = "_".join(idx.columns)
        idx_name = f"idx_{bare_table}_{idx_cols}"
        idx_cols_sql = ", ".join(f'"{c}"' for c in idx.columns)
        unique_kw = "UNIQUE " if idx.unique else ""
        ddl += f'\nCREATE {unique_kw}INDEX IF NOT EXISTS "{idx_name}" ON "{db_schema}"."{bare_table}" ({idx_cols_sql});'

    return ddl


def propose_file_schema(schema_summary: dict, purpose: str, reads_from: list, destination_table: str, earlier_file_schemas: dict = None) -> dict:
    """Returns {"schema": ProposedFileSchema.model_dump(), "ddl": <rendered CREATE TABLE text>}.

    earlier_file_schemas is an explicit, separate dict of
    {destination_table: ddl_text} for earlier, already-approved files in
    this same build plan - never folded into schema_summary itself,
    since schema_summary specifically means "real tables available to
    READ from" (the exact contract propose_architecture.py and
    generate_pipeline.py already establish and depend on). An earlier
    file's own DDL is a genuinely different kind of context (what
    already exists as a valid FOREIGN KEY target), and disguising it as
    a fake schema_summary entry would risk the AI treating it as
    readable source data rather than what it actually is.
    """
    user_content = json.dumps({
        "schema_summary": schema_summary,
        "purpose": purpose,
        "reads_from": reads_from,
        "destination_table": destination_table,
        "earlier_file_schemas": earlier_file_schemas or {},
    }, default=str)

    if anthropic_client:
        try:
            print("[Schema Proposer] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            response = call_with_rate_limit_backoff(lambda: anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=8000,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=ProposedFileSchema,
            ))
            if response.parsed_output is None:
                raise RuntimeError(
                    f"Claude did not return a complete structured response "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})."
                )
            proposed = response.parsed_output
        except Exception as e:
            print(f"[Warning] Anthropic schema proposal failed: {e}")
            print("[Schema Proposer] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")
            proposed = None
    else:
        proposed = None

    if proposed is None:
        if not openai_client:
            raise ValueError("No AI provider is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=ProposedFileSchema,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(
                    response.choices[0].message.refusal
                    or "OpenAI declined to produce a structured response."
                )
            proposed = parsed
        except Exception as e:
            raise RuntimeError(f"OpenAI fallback schema proposal failed: {e}")

    ddl = render_ddl(destination_table, proposed)
    return {"schema": proposed.model_dump(), "ddl": ddl}


if __name__ == "__main__":
    from introspect import introspect_schema

    print("Introspecting schema...")
    schema_summary = introspect_schema()

    purpose = input("File purpose (plain English): ")
    reads_from = input("Reads from (comma-separated real table names): ").split(",")
    destination_table = input("Destination table (schema.table): ")

    result = propose_file_schema(schema_summary, purpose, [t.strip() for t in reads_from], destination_table)
    print("\n--- Rationale ---")
    print(result["schema"]["rationale"])
    print("\n--- DDL ---")
    print(result["ddl"])
