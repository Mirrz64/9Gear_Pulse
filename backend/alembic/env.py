import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Make backend/ importable so `from db.models import Base` resolves
# regardless of the working directory alembic is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Base  # noqa: E402

# Docker Compose provides an in-network DATABASE_URL for the backend
# container. Do not let a developer's host-only .env URL overwrite it.
# Missing variables can still be supplied from .env for local execution.
load_dotenv(override=False)

config = context.config

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Alembic needs a real postgresql:// "
        "connection string in your .env to autogenerate or apply migrations."
    )
if not db_url.startswith("postgresql"):
    raise RuntimeError(
        f"DATABASE_URL is set to '{db_url}', which is not a postgresql:// "
        "URL. This schema (db/models.py) is Postgres-only - pointing "
        "Alembic at anything else (e.g. the SQLite file the existing "
        "prototype uses) will generate a migration that tries to destroy "
        "and rewrite THAT database's tables instead. Point DATABASE_URL "
        "at your docker-compose Postgres service before running Alembic."
    )
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
