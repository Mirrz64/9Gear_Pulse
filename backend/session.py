"""
Database engine/session setup for the new commercial schema.

Deliberately separate from backend/introspect.py's get_db_url() (which
supports both SQLite and Postgres, for the old single-connection
prototype). This schema is Postgres-only - see the note at the top of
models.py for why.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError(
        "DATABASE_URL must be a postgresql:// connection string for the "
        "new commercial schema (db/models.py) - it uses native UUID/JSONB/"
        "ENUM types SQLite can't represent. Point it at the docker-compose "
        "Postgres service, e.g.\n"
        "  postgresql://pulse:pulse_secure_password@localhost:5432/pulse_audit\n"
        "when running outside Docker, or reuse the DATABASE_URL "
        "docker-compose.yml already injects when running inside it. "
        "This does not affect the existing SQLite-based prototype in "
        "app.py/introspect.py, which is untouched."
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency: yields a Session, always closes it after the request.

    Usage once this is wired into app.py:
        @app.get("/api/v2/projects")
        def list_projects(db: Session = Depends(get_db)):
            ...
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
