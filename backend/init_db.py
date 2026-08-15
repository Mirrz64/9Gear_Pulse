import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    text,
)

# Load backend .env variables
load_dotenv()

# Fallback to local SQLite if DATABASE_URL isn't explicitly set in backend/.env
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./9gear_pulse.db")

print(f"🔌 Target Database: {DATABASE_URL}")

# Create engine with sqlite compatibility flag if needed
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
metadata = MetaData()

# -------------------------------------------------------------------
# Table Definitions
# -------------------------------------------------------------------

# 1. Pipeline Audit Logs (Core engine tracking)
pipeline_audit_logs = Table(
    "pipeline_audit_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("pipeline_name", String(255), nullable=False),
    Column("status", String(50), nullable=False),  # SUCCESS, FAILED, RUNNING
    Column("attempts", Integer, default=1),
    Column("execution_time", DateTime, default=lambda: datetime.now(timezone.utc)),
    Column("logs", Text, nullable=True),
)

# 2. Pipeline Schedules
pipeline_schedules = Table(
    "pipeline_schedules",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("goal", Text, nullable=False),
    Column("interval_minutes", Integer, nullable=False),
    Column("trigger", String(50), default="interval"),
    Column("next_run_time", DateTime, nullable=False),
    Column("created_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)

# 3. Sample Domain Table: Source Users
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(100), nullable=False, unique=True),
    Column("email", String(255), nullable=False),
    Column("role", String(50), default="user"),
    Column("created_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)

# 4. Sample Domain Table: Analytics Target Table
analytics_reporting = Table(
    "analytics_reporting",
    metadata,
    Column("report_id", Integer, primary_key=True, autoincrement=True),
    Column("metric_name", String(100), nullable=False),
    Column("metric_value", String(255), nullable=False),
    Column("processed_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)


# -------------------------------------------------------------------
# Initialization & Seeding Logic
# -------------------------------------------------------------------


def init_and_seed_db():
    print("🔨 Creating schema tables if they do not exist...")
    metadata.create_all(engine)
    print("✅ Tables created successfully.")

    with engine.begin() as conn:
        inspector = inspect(engine)

        # -----------------------------------------------------------
        # Seed 1: Seed Users Table
        # -----------------------------------------------------------
        users_count = conn.execute(
            text("SELECT COUNT(*) FROM users")
        ).scalar()
        if users_count == 0:
            print("🌱 Seeding 'users' table...")
            conn.execute(
                users.insert(),
                [
                    {
                        "username": "data_admin",
                        "email": "admin@9gear.io",
                        "role": "admin",
                        "created_at": datetime.now(timezone.utc)
                        - timedelta(days=30),
                    },
                    {
                        "username": "etl_runner",
                        "email": "runner@9gear.io",
                        "role": "service_account",
                        "created_at": datetime.now(timezone.utc)
                        - timedelta(days=15),
                    },
                    {
                        "username": "analyst_usr",
                        "email": "analyst@9gear.io",
                        "role": "read_only",
                        "created_at": datetime.now(timezone.utc),
                    },
                ],
            )
            print("  └─ Inserted 3 initial users.")
        else:
            print("ℹ️ 'users' table already contains data. Skipping seed.")

        # -----------------------------------------------------------
        # Seed 2: Seed Audit Logs Table
        # -----------------------------------------------------------
        logs_count = conn.execute(
            text("SELECT COUNT(*) FROM pipeline_audit_logs")
        ).scalar()
        if logs_count == 0:
            print("🌱 Seeding 'pipeline_audit_logs' table...")
            now = datetime.now(timezone.utc)
            conn.execute(
                pipeline_audit_logs.insert(),
                [
                    {
                        "pipeline_name": "users_to_analytics_ingest",
                        "status": "SUCCESS",
                        "attempts": 1,
                        "execution_time": now - timedelta(hours=2),
                        "logs": "[INIT] Extraction started from 'users'\n[INFO] Fetched 3 records.\n[SUCCESS] Pipeline target updated.",
                    },
                    {
                        "pipeline_name": "daily_metric_rollup",
                        "status": "FAILED",
                        "attempts": 3,
                        "execution_time": now - timedelta(hours=1),
                        "logs": "[INIT] Starting aggregation\n[ERROR] ConnectionTimeout: Target schema locked by concurrent process.\n[FAIL] Max retries exhausted.",
                    },
                ],
            )
            print("  └─ Inserted 2 sample audit records.")
        else:
            print(
                "ℹ️ 'pipeline_audit_logs' table already contains data. Skipping seed."
            )

        # -----------------------------------------------------------
        # Seed 3: Seed Schedules Table
        # -----------------------------------------------------------
        schedules_count = conn.execute(
            text("SELECT COUNT(*) FROM pipeline_schedules")
        ).scalar()
        if schedules_count == 0:
            print("🌱 Seeding 'pipeline_schedules' table...")
            conn.execute(
                pipeline_schedules.insert(),
                [
                    {
                        "id": "job_users_sync_30m",
                        "goal": "Extract all records from users table and update analytics_reporting",
                        "interval_minutes": 30,
                        "trigger": "interval",
                        "next_run_time": datetime.now(timezone.utc)
                        + timedelta(minutes=30),
                        "created_at": datetime.now(timezone.utc),
                    }
                ],
            )
            print("  └─ Inserted 1 active scheduled job.")
        else:
            print(
                "ℹ️ 'pipeline_schedules' table already contains data. Skipping seed."
            )

    print("\n🎉 Database initialization and seeding complete!")


if __name__ == "__main__":
    init_and_seed_db()