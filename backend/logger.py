import os
import json
import datetime
from sqlalchemy import create_engine, text

def log_pipeline_run(
    pipeline_name: str,
    status: str,
    attempts: int,
    logs: str,
    error_summary: str = None
):
    """Logs pipeline run metrics to the target PostgreSQL audit table."""
    dest_db_url = os.environ.get("DEST_DB_URL")
    if not dest_db_url:
        print("[Logger Warning]: DEST_DB_URL not set. Skipping DB audit log.")
        return

    engine = create_engine(dest_db_url)
    
    # Ensure audit schema & table exist
    setup_sql = text("""
        CREATE SCHEMA IF NOT EXISTS _pipeline_audit;
        CREATE TABLE IF NOT EXISTS _pipeline_audit.execution_logs (
            id SERIAL PRIMARY KEY,
            pipeline_name VARCHAR(255) NOT NULL,
            status VARCHAR(50) NOT NULL,
            attempts INT NOT NULL,
            execution_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            logs TEXT,
            error_summary TEXT
        );
    """)

    insert_sql = text("""
        INSERT INTO _pipeline_audit.execution_logs 
        (pipeline_name, status, attempts, logs, error_summary)
        VALUES (:name, :status, :attempts, :logs, :error);
    """)

    try:
        with engine.connect() as conn:
            conn.execute(setup_sql)
            conn.execute(insert_sql, {
                "name": pipeline_name,
                "status": status,
                "attempts": attempts,
                "logs": logs,
                "error": error_summary
            })
            conn.commit()
        print(f"[Logger]: Successfully logged run status '{status}' to _pipeline_audit.execution_logs")
    except Exception as e:
        print(f"[Logger Error]: Failed to write audit log: {e}")