"""One-off: peek at real sample values for the two columns whose
encoding is currently just an assumption (bronze_events.value,
bronze_offers.channels) - purely for a human to read and then
describe precisely in the project goal text.

This never touches the app's stored schema_metadata_json (that stays
sample_rows=0, unchanged) and never gets sent to the AI - this prints
straight to your own terminal, nothing else.

Run from anywhere with the venv active (doesn't need to be backend/
specifically, but does need sqlalchemy/psycopg2 installed there).
"""
import sqlalchemy as sa

DB_URL = "postgresql://events_pipeline_user:events_pipeline_pass@127.0.0.1:5433/events_pipeline_raw"

engine = sa.create_engine(DB_URL)
with engine.connect() as conn:
    print("--- bronze.bronze_events.value (5 samples) ---")
    for row in conn.execute(sa.text("SELECT value FROM bronze.bronze_events LIMIT 5")):
        print(repr(row[0]))

    print("\n--- bronze.bronze_offers.channels (all 10 - it's a small table) ---")
    for row in conn.execute(sa.text("SELECT offer_id, channels FROM bronze.bronze_offers")):
        print(row[0], repr(row[1]))
