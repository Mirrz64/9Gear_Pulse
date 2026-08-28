"""
Post-load data-quality checks, run by the backend against the real
destination table after a successful sandbox test - not something the
AI-generated pipeline script is asked to implement itself.

Deliberately simple for a first slice: row count sanity and per-column
null-rate scanning, both generic enough to apply to any table without
needing to know anything domain-specific about it. Results are surfaced
to the human reviewer as warnings, never used to auto-fail a run or block
approval - matching the review-gate's whole premise that a human makes
the final call, not an automated check.
"""
from typing import Any

from sqlalchemy import create_engine, inspect, text

# A load that reports success but wrote zero rows is usually worth a
# second look - either the source really was empty, or something silently
# dropped everything.
_ROW_COUNT_ZERO_WARNING = "Load reported success but the destination table has 0 rows."
# Somewhat arbitrary, deliberately conservative threshold - flagging
# earlier would likely just be noisy for legitimately-sparse columns.
_NULL_RATE_WARNING_THRESHOLD = 0.5


def run_quality_checks(destination_url: str, dataset: str, table: str) -> dict[str, Any]:
    """Connects directly to the destination database and inspects the
    exact table the AI reported writing to. Returns a plain dict, stored
    as-is on PipelineRun.quality_checks (JSONB) - never raises for a
    "bad" result, only for a genuine connection/inspection failure, since
    a quality check that itself crashes the whole test run would defeat
    the point of it being informational rather than blocking.
    """
    engine = create_engine(destination_url)
    warnings: list[str] = []
    columns: dict[str, dict[str, Any]] = {}
    row_count = 0

    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            table_names = inspector.get_table_names(schema=dataset)
            if table not in table_names:
                return {
                    "checked": False,
                    "reason": f"Table '{dataset}.{table}' was not found after the load completed.",
                }

            qualified = f'"{dataset}"."{table}"' if dataset else f'"{table}"'
            row_count = conn.execute(text(f"SELECT COUNT(*) FROM {qualified}")).scalar() or 0

            if row_count == 0:
                warnings.append(_ROW_COUNT_ZERO_WARNING)

            for col in inspector.get_columns(table, schema=dataset):
                col_name = col["name"]
                if row_count == 0:
                    columns[col_name] = {"null_count": 0, "null_rate": None}
                    continue
                null_count = conn.execute(
                    text(f'SELECT COUNT(*) FROM {qualified} WHERE "{col_name}" IS NULL')
                ).scalar() or 0
                null_rate = null_count / row_count
                columns[col_name] = {"null_count": null_count, "null_rate": round(null_rate, 4)}
                if null_rate >= _NULL_RATE_WARNING_THRESHOLD:
                    warnings.append(
                        f"Column '{col_name}' is {null_rate:.0%} null "
                        f"({null_count} of {row_count} rows)."
                    )
    finally:
        engine.dispose()

    return {
        "checked": True,
        "dataset": dataset,
        "table": table,
        "row_count": row_count,
        "columns": columns,
        "warnings": warnings,
    }
