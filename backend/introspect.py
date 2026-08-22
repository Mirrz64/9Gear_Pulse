"""
Schema introspection for 9Gear Pulse — Phase 1 of the AI-to-pipeline engine.

Connects using environment variables or DATABASE_URL/SOURCE_DB_URL.
Returns a compact JSON summary of tables/columns/types/row counts/sample rows.
"""
import datetime
import os
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

# Compose supplies an in-network DATABASE_URL. A host-only URL in .env must
# not replace it once this module is running inside the backend container.
load_dotenv(override=False)


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)


def get_db_url() -> str:
    """Resolves the database URL from environment variables, handling hostnames,

    Postgres parameters, and SQLite fallbacks seamlessly.
    """
    # 1. Check direct URL strings
    url = os.getenv("SOURCE_DB_URL") or os.getenv("DATABASE_URL")
    if url:
        # If running locally outside docker, translate container host '9gear_pulse_db' to localhost
        if "9gear_pulse_db" in url and not os.getenv("RUNNING_IN_DOCKER"):
            url = url.replace("9gear_pulse_db", "localhost")
        return url

    # 2. Check individual Postgres ENV vars
    pg_host = os.getenv("PG_HOST", "localhost")
    if pg_host == "9gear_pulse_db" and not os.getenv("RUNNING_IN_DOCKER"):
        pg_host = "localhost"

    pg_user = os.getenv("PG_USER", "pulse")
    pg_pass = os.getenv("PG_PASSWORD", "pulse_secure_password")
    pg_port = os.getenv("PG_PORT", "5432")
    pg_db = os.getenv("PG_DATABASE") or os.getenv("PG_DB", "pulse_audit")

    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


def introspect_schema(schema_name: str = None, sample_rows: int = 3, db_url: str = None) -> dict:
    """Returns: { full_table_name: { columns: [...], row_count: int, sample: [...] } }

    Works with both PostgreSQL and SQLite backends via SQLAlchemy reflection.
    """
    db_url = db_url or get_db_url()
    engine_kwargs = {}

    if db_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    try:
        engine = create_engine(db_url, **engine_kwargs)
        inspector = inspect(engine)
        table_names = inspector.get_table_names(schema=schema_name)
    except Exception as e:
        print(f"⚠️ Primary DB connection failed ({db_url}): {e}")
        print("🔄 Falling back to local SQLite database (9gear_pulse.db)...")
        db_url = "sqlite:///./9gear_pulse.db"
        engine = create_engine(
            db_url, connect_args={"check_same_thread": False}
        )
        inspector = inspect(engine)
        table_names = inspector.get_table_names()

    tables: dict = {}

    with engine.connect() as conn:
        for t_name in table_names:
            s_name = schema_name or "public"
            full_key = f"{s_name}.{t_name}" if "sqlite" not in db_url else t_name

            # Column extraction
            raw_columns = inspector.get_columns(t_name, schema=schema_name)
            columns = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in raw_columns
            ]

            # Row count & Sample row extraction
            row_count = 0
            sample_data = []

            try:
                # Row Count
                count_res = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{t_name}"')
                )
                row_count = count_res.scalar() or 0

                # Samples
                if sample_rows > 0:
                    sample_res = conn.execute(
                        text(f'SELECT * FROM "{t_name}" LIMIT {sample_rows}')
                    )
                    sample_data = [
                        dict(row._mapping) for row in sample_res.fetchall()
                    ]
            except Exception:
                row_count = 0
                sample_data = []

            tables[full_key] = {
                "schema": s_name,
                "table": t_name,
                "columns": columns,
                "row_count": row_count,
                "sample": sample_data,
            }

    return tables


if __name__ == "__main__":
    import json

    schema = introspect_schema()
    print(json.dumps(schema, indent=2, default=json_serial))
