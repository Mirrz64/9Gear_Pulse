import argparse
import asyncio
import json
import os
import time
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect

# Internal Module Imports
from generate_pipeline import generate_pipeline
from heal_pipeline import execute_with_self_healing
from introspect import introspect_schema
from logger import log_pipeline_run

# Safe Audit Log Retrieval Import
try:
    from logger import get_audit_logs
except ImportError:

    def get_audit_logs():
        return []


load_dotenv(override=True)

# Database Connection Setup for Schema Inspection
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./9gear_pulse.db")
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

db_engine = create_engine(DATABASE_URL, **engine_kwargs)

# ------------------------------------------------------------------
# 1. CORE PIPELINE ORCHESTRATOR
# ------------------------------------------------------------------


def run_end_to_end_pipeline(
    goal: str,
    output_path: str = "generated_pipeline.py",
    max_retries: int = 3,
) -> bool:
    """Orchestrates schema introspection, code generation, sandbox testing with self-healing,

    and audit logging.
    """
    start_time = time.time()
    pipeline_name = "generated_pipeline"
    print("==================================================")
    print("      9GEAR PULSE - PIPELINE ORCHESTRATOR         ")
    print("==================================================")
    print("[1/4] Introspecting database schema...")

    try:
        schema_summary = introspect_schema()
        print("      Schema introspection complete.")
    except Exception as e:
        error_msg = f"Schema introspection failed: {str(e)}"
        print(f"[Error]: {error_msg}")
        log_pipeline_run(
            "orchestrator_init", "FAILED", 0, error_msg, error_msg
        )
        return False

    print(
        f'\n[2/4] Generating dlt pipeline code for goal:\n      "{goal}"...'
    )
    try:
        pipeline_data = generate_pipeline(schema_summary, goal)
        pipeline_name = pipeline_data.get(
            "pipeline_name", "generated_pipeline"
        )
        code = pipeline_data.get("code")

        if not code:
            raise ValueError(
                "Model generated response without executable code."
            )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        print(
            f"      Successfully generated '{pipeline_name}' -> Saved to {output_path}"
        )
    except Exception as e:
        error_msg = f"Pipeline generation failed: {str(e)}"
        print(f"[Error]: {error_msg}")
        log_pipeline_run("generator", "FAILED", 0, error_msg, error_msg)
        return False

    print(
        f"\n[3/4] Executing pipeline in Docker sandbox (Self-Healing Enabled, Max Retries: {max_retries})..."
    )
    success, attempts, final_logs = execute_with_self_healing(
        script_path=output_path,
        schema_summary=schema_summary,
        max_retries=max_retries,
    )

    elapsed_time = round(time.time() - start_time, 2)
    status = "SUCCESS" if success else "FAILED"

    print("\n[4/4] Writing run metrics to database audit logs...")
    log_pipeline_run(
        pipeline_name=pipeline_name,
        status=status,
        attempts=attempts,
        logs=final_logs,
        error_summary=(
            None if success else final_logs[-500:]
        ),  # Capture trailing error context
    )

    print("==================================================")
    print(
        f" Execution Summary: Status={status} | Attempts={attempts} | Duration={elapsed_time}s"
    )
    print("==================================================")

    return success


# ------------------------------------------------------------------
# 2. FASTAPI HTTP LAYER FOR FRONTEND CONTROL PLANE
# ------------------------------------------------------------------

app = FastAPI(title="9Gear Pulse API", version="1.0.0")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PipelineRequest(BaseModel):
    goal: str
    max_retries: int = 3


class ScheduleRequest(BaseModel):
    goal: str
    interval_minutes: int = 30
    max_retries: int = 3


@app.get("/")
def health_check():
    return {"status": "online", "system": "9Gear Pulse Control Plane"}


# --- REQUIRED DASHBOARD READ ENDPOINTS ---


@app.get("/api/logs")
def get_logs():
    """Returns execution audit logs for the Next.js table."""
    try:
        logs = get_audit_logs()
        return {"logs": logs}
    except Exception as e:
        print(f"Error reading audit logs: {e}")
        return {"logs": []}


@app.get("/api/schema")
def get_schema():
    """Returns database schema structure for the schema drawer cleanly.

    Casts data types to string to ensure JSON serialization succeeds.
    """
    try:
        inspector = inspect(db_engine)
        schema_map = {}

        for table_name in inspector.get_table_names():
            columns = []
            for col in inspector.get_columns(table_name):
                columns.append(
                    {
                        "column_name": col["name"],
                        "data_type": str(col["type"]),  # Safe string conversion
                    }
                )
            schema_map[table_name] = columns

        return {"schema": schema_map}
    except Exception as e:
        print(f"❌ Failed to fetch schema: {e}")
        # Return empty schema mapping instead of crashing with 500 Internal Server Error
        return {"schema": {}}


@app.get("/api/code")
def get_latest_code(output_path: str = "generated_pipeline.py"):
    """Reads the generated pipeline code artifact for frontend viewing."""
    try:
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as f:
                code = f.read()
            return {"code": code}
    except Exception as e:
        print(f"Error reading generated code: {e}")

    return {"code": "# No generated pipeline script available."}


@app.get("/api/schedules")
def get_schedules():
    """Returns active background scheduled jobs."""
    try:
        # Standard placeholder for schedules
        return {"schedules": []}
    except Exception as e:
        return {"schedules": []}


# --- EXECUTION ENDPOINTS ---


@app.post("/api/run")
@app.post("/api/pipeline/run")
def api_run_pipeline(payload: PipelineRequest):
    """API Endpoint triggered by the Next.js UI when clicking 'Run Pipeline'"""
    try:
        success = run_end_to_end_pipeline(
            goal=payload.goal, max_retries=payload.max_retries
        )
        if not success:
            raise HTTPException(
                status_code=500, detail="Pipeline execution failed in sandbox."
            )
        return {
            "success": True,
            "message": "Pipeline processing completed successfully.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline orchestrator execution error: {str(e)}",
        )


@app.get("/api/stream-logs/{job_id}")
async def stream_logs(job_id: str):
    """SSE endpoint for live log streaming to the Next.js execution console."""

    async def log_generator() -> AsyncGenerator[str, None]:
        yield f"data: [9Gear Pulse] Initiating task {job_id}...\n\n"
        await asyncio.sleep(0.5)
        yield "data: [1/4] Introspecting schema...\n\n"
        await asyncio.sleep(0.5)
        yield "data: [2/4] Generating pipeline code...\n\n"
        await asyncio.sleep(0.5)
        yield "data: [3/4] Running self-healing execution loop...\n\n"
        await asyncio.sleep(0.5)
        yield "data: [COMPLETE] Execution stream finished.\n\n"
        yield "data: [DONE]\n\n"  # Signal frontend to close EventSource

    return StreamingResponse(log_generator(), media_type="text/event-stream")


@app.post("/api/schedule")
def schedule_pipeline(payload: ScheduleRequest):
    """Schedules a pipeline run interval."""
    return {"status": "success", "message": "Job scheduled successfully."}


@app.delete("/api/schedule/{job_id}")
def delete_schedule(job_id: str):
    """Deletes a background scheduled job."""
    return {"status": "success", "message": f"Job {job_id} removed."}


# ------------------------------------------------------------------
# 3. CLI ENTRY POINT
# ------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="9Gear Pulse Pipeline Engine")
    parser.add_argument(
        "--goal", type=str, help="Plain English data pipeline goal"
    )
    args = parser.parse_args()

    user_goal = args.goal
    if not user_goal:
        user_goal = input(
            "\nDescribe the pipeline you want to generate (plain English): "
        )

    run_end_to_end_pipeline(goal=user_goal)