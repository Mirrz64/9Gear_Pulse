"""One-off: call run_quality_checks() directly against an already-loaded
table, to verify the destination_table splitting fix independent of the
review API. Run from backend/ with the venv active.
"""
from data_quality import run_quality_checks

result = run_quality_checks(
    "postgresql://events_pipeline_user:events_pipeline_pass@127.0.0.1:5433/events_pipeline_raw",
    "silver", "silver_offer_events",
)
print(result)
