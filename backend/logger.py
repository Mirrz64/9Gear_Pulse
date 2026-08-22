import os
from sqlalchemy import create_engine, text

from introspect import get_db_url

def _resolve_db_url() -> str:
    """DEST_DB_URL wins when explicitly set (useful if source and
    destination really are different databases). Otherwise falls back to
    the same resolution introspect.py already uses for the source DB, so
    this works out of the box whether you're on local SQLite or Postgres
    via docker-compose, without requiring DEST_DB_URL to be configured
    separately.
    """
    return os.environ.get("DEST_DB_URL") or get_db_url()


def _get_engine(db_url: str):
    is_sqlite = db_url.startswith("sqlite")
    engine_kwargs = {"connect_args": {"check_same_thread": False}} if is_sqlite else {}
    return create_engine(db_url, **engine_kwargs), is_sqlite


def _ensure_audit_table(conn, is_sqlite: bool):
    """Creates pipeline_audit_logs if it doesn't exist yet. Plain CREATE
    TABLE (no CREATE SCHEMA), so it works identically on SQLite and
    Postgres, and matches the exact table init_db.py already creates.
    """
    if is_sqlite:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pipeline_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_name TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                logs TEXT
            );
        """))
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pipeline_audit_logs (
                id SERIAL PRIMARY KEY,
                pipeline_name VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                attempts INT NOT NULL,
                execution_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                logs TEXT
            );
        """))


def log_pipeline_run(
    pipeline_name: str,
    status: str,
    attempts: int,
    logs: str,
    error_summary: str = None
):
    """Logs a pipeline run to pipeline_audit_logs — the same table
    init_db.py seeds and the dashboard's /api/logs reads from.
    """
    db_url = _resolve_db_url()
    engine, is_sqlite = _get_engine(db_url)

    # pipeline_audit_logs has no separate error_summary column (matching
    # init_db.py's schema) — fold it into logs instead of dropping it.
    full_logs = logs if not error_summary else f"{logs}\n[ERROR SUMMARY]: {error_summary}"

    insert_sql = text("""
        INSERT INTO pipeline_audit_logs (pipeline_name, status, attempts, logs)
        VALUES (:name, :status, :attempts, :logs);
    """)

    try:
        with engine.connect() as conn:
            _ensure_audit_table(conn, is_sqlite)
            conn.execute(insert_sql, {
                "name": pipeline_name,
                "status": status,
                "attempts": attempts,
                "logs": full_logs,
            })
            conn.commit()
        print(f"[Logger]: Successfully logged run status '{status}' to pipeline_audit_logs")
    except Exception as e:
        print(f"[Logger Error]: Failed to write audit log: {e}")


def get_audit_logs(limit: int = 20) -> list[dict]:
    """Reads the most recent audit log records for the dashboard table.
    This is the function main.py was previously trying (and failing) to
    import — it didn't exist here before.
    """
    db_url = _resolve_db_url()
    engine, is_sqlite = _get_engine(db_url)

    try:
        with engine.connect() as conn:
            _ensure_audit_table(conn, is_sqlite)
            result = conn.execute(
                text(
                    "SELECT id, pipeline_name, status, attempts, execution_time, logs "
                    "FROM pipeline_audit_logs ORDER BY id DESC LIMIT :limit;"
                ),
                {"limit": limit},
            )
            return [dict(row._mapping) for row in result]
    except Exception as e:
        print(f"[Logger Error]: Failed to read audit logs: {e}")
        return []
