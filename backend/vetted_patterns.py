"""
9Gear Pulse - vetted reference patterns.

The lighter version of "Blueprint-style guardrails": a small library of
well-understood, correctly-implemented ETL patterns that the architecture-
review and code-generation prompts are encouraged to recognize and reuse
when genuinely applicable - not a closed registry either stage is
constrained to select from. A goal that doesn't fit any of these is still
handled by writing custom logic freely, exactly as before this existed.

Deliberately small and concrete rather than an attempt to enumerate every
ETL pattern in the abstract - each one here maps directly to a real,
specific lesson from an actual project (Triplens GCE), not a generic
textbook pattern picked without evidence anyone needed it.
"""

VETTED_PATTERNS = [
    {
        "name": "dedupe_by_natural_key",
        "when": (
            "Deduplicating records where the field that looks like the "
            "obvious unique identifier can legitimately be blank or "
            "inconsistent for some real records (e.g. a reference dataset "
            "with a few edge-case entries missing a standard code)."
        ),
        "snippet": '''seen = set()
def dedupe_by_key(records, key_fn):
    """key_fn should extract a field that's reliably present on every
    real record - not necessarily the field that looks like the ID,
    if that field can legitimately be blank for some of them."""
    for record in records:
        key = key_fn(record)
        if key in seen:
            continue
        seen.add(key)
        yield record''',
    },
    {
        "name": "one_to_many_bridge_table",
        "when": (
            "An entity has an attribute that can hold more than one value "
            "(e.g. a record with multiple languages, categories, or tags) "
            "- this becomes its own table, one row per (parent_key, value) "
            "pair, not a single denormalized column crammed with several "
            "values."
        ),
        "snippet": '''@dlt.resource(table_name="entity_attribute_values", write_disposition="merge", primary_key=["parent_key", "value"])
def entity_attribute_values(entities):
    for entity in entities:
        for value in entity.get("attribute_list", []):
            yield {"parent_key": entity["natural_key"], "value": value}''',
    },
    {
        "name": "incremental_upsert",
        "when": (
            'The project\'s stated load_pattern is "incremental" - update '
            "changed records and add new ones, based on a stable key, "
            "without a full reload each run."
        ),
        "snippet": '''@dlt.resource(write_disposition="merge", primary_key="id")
def my_resource():
    ...''',
    },
    {
        "name": "full_refresh_replace",
        "when": (
            'The project\'s stated load_pattern is "full_refresh" - replace '
            "the destination table entirely each run. The right choice when "
            "the full upstream dataset is re-pulled wholesale every time "
            "and there's no reason to preserve rows the latest pull no "
            "longer returns."
        ),
        "snippet": '''@dlt.resource(write_disposition="replace")
def my_resource():
    ...''',
    },
    {
        "name": "graceful_missing_field",
        "when": (
            "Some real records legitimately lack a field others have (e.g. "
            "a reference entity with no value for an otherwise-common "
            "attribute). Never assume every field is always present, and "
            "never let one record's missing field crash the whole load."
        ),
        "snippet": '''value = record.get("field_name") or None''',
    },
]


def patterns_for_architecture_review() -> str:
    """Names and recognition criteria only - no code. This is what the
    architecture-review stage sees: encouragement to *name* a pattern in
    key_transformations/approach when a step genuinely matches one, so
    code generation downstream knows to apply the vetted, correct
    implementation rather than reinventing it from scratch.
    """
    lines = []
    for p in VETTED_PATTERNS:
        lines.append(f'- "{p["name"]}": {p["when"]}')
    return "\n".join(lines)


def patterns_for_code_generation() -> str:
    """Names, recognition criteria, and the actual reference snippet -
    what code generation sees, so a step the plan already named as
    matching one of these gets the vetted, correct implementation
    adapted to the real field/table names, not a fresh reinvention that
    might repeat a mistake this library exists specifically to avoid.
    """
    blocks = []
    for p in VETTED_PATTERNS:
        blocks.append(
            f'"{p["name"]}" - {p["when"]}\n{p["snippet"]}'
        )
    return "\n\n".join(blocks)
