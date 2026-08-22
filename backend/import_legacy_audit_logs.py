"""One-time, idempotent import of the prototype SQLite audit history.

Run inside the backend container after upgrading to the PostgreSQL control
plane. This retains the dashboard history generated before the Alembic schema
was introduced; it does not migrate credentials or pipeline definitions.
"""
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, text

from introspect import get_db_url
from logger import _ensure_audit_table

LEGACY_DB = Path(__file__).with_name("9gear_pulse.db")


def main() -> None:
    if not LEGACY_DB.exists():
        raise SystemExit(f"Legacy database not found: {LEGACY_DB}")

    destination_url = get_db_url()
    if destination_url.startswith("sqlite"):
        raise SystemExit("Set DATABASE_URL to PostgreSQL before importing audit history.")

    with sqlite3.connect(LEGACY_DB) as source:
        source.row_factory = sqlite3.Row
        rows = source.execute(
            "SELECT pipeline_name, status, attempts, execution_time, logs FROM pipeline_audit_logs"
        ).fetchall()

    engine = create_engine(destination_url)
    imported = 0
    with engine.begin() as destination:
        _ensure_audit_table(destination, is_sqlite=False)
        for row in rows:
            already_imported = destination.execute(
                text("""
                    SELECT 1 FROM pipeline_audit_logs
                    WHERE pipeline_name = :pipeline_name
                      AND status = :status
                      AND attempts = :attempts
                      AND logs IS NOT DISTINCT FROM :logs
                    LIMIT 1
                """),
                dict(row),
            ).scalar()
            if already_imported:
                continue
            values = dict(row)
            if values["execution_time"]:
                destination.execute(text("""
                    INSERT INTO pipeline_audit_logs
                    (pipeline_name, status, attempts, execution_time, logs)
                    VALUES (:pipeline_name, :status, :attempts, :execution_time, :logs)
                """), values)
            else:
                destination.execute(text("""
                    INSERT INTO pipeline_audit_logs (pipeline_name, status, attempts, logs)
                    VALUES (:pipeline_name, :status, :attempts, :logs)
                """), values)
            imported += 1
    print(f"Imported {imported} legacy audit log(s) into PostgreSQL.")


if __name__ == "__main__":
    main()
