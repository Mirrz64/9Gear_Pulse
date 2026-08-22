import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from main import run_end_to_end_pipeline
from introspect import introspect_schema
from logger import get_audit_logs

if os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    from schedule_service import scheduler, restore_schedules
else:
    scheduler = BackgroundScheduler()
    restore_schedules = None

app = FastAPI(
    title="9Gear Pulse API",
    description="Control plane for AI-generated ETL pipelines and execution auditing",
    version="1.0.0"
)

@app.on_event("startup")
def start_scheduler():
    if not scheduler.running:
        scheduler.start()
    if restore_schedules:
        restore_schedules()

@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

# Allow CORS for Next.js web frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# The legacy prototype can still run against its SQLite database. The review
# gate deliberately appears only when this service is pointed at Postgres.
if os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    from review_api import router as review_router
    app.include_router(review_router)

class PipelineRequest(BaseModel):
    goal: str
    max_retries: int = 3

class ScheduleRequest(BaseModel):
    goal: str
    interval_minutes: int = 60
    max_retries: int = 3

@app.get("/")
def read_root():
    return {"status": "online", "service": "9Gear Pulse Engine"}

@app.get("/api/schema")
def get_schema():
    """Returns database schema metadata from introspection mapped for the control plane UI."""
    try:
        raw_schema = introspect_schema()
        formatted_schema = {}
        
        for full_table_name, details in raw_schema.items():
            formatted_schema[full_table_name] = [
                {
                    "column_name": col["name"],
                    "data_type": col["type"]
                }
                for col in details.get("columns", [])
            ]
            
        return {"schema": formatted_schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/code")
def get_generated_code():
    """Returns the latest generated pipeline Python code from disk."""
    code_path = "generated_pipeline.py"
    if not os.path.exists(code_path):
        return {"code": "# No generated pipeline script found on disk."}
    try:
        with open(code_path, "r", encoding="utf-8") as f:
            code = f.read()
        return {"code": code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read code artifact: {str(e)}")

@app.post("/api/run")
def trigger_pipeline(payload: PipelineRequest):
    """Triggers schema introspection, pipeline generation, Docker sandboxing, and audit logging."""
    # Previously had no try/except at all, so an unhandled exception (e.g.
    # Docker not running) escaped as Starlette's default plain-text 500
    # response instead of JSON - the frontend's `await res.json()` then
    # throws on that non-JSON body and lands in its catch block, showing
    # the generic "Failed to connect to FastAPI backend" even when the
    # backend was reachable and the real cause was something else.
    try:
        success = run_end_to_end_pipeline(goal=payload.goal, max_retries=payload.max_retries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline orchestrator error: {str(e)}")
    if not success:
        raise HTTPException(status_code=500, detail="Pipeline execution or self-healing failed. Check the audit logs for details.")
    return {"status": "SUCCESS", "goal": payload.goal}

@app.get("/api/stream-logs/{job_id}")
async def stream_pipeline_logs(job_id: str):
    """Streams live execution logs from a running Docker pipeline container."""
    async def log_generator():
        yield f"data: [INIT] Connecting to execution sandbox for job {job_id}...\n\n"
        await asyncio.sleep(0.5)
        
        try:
            recent = get_audit_logs(limit=1)
            if recent:
                logs = recent[0].get("logs", "") or ""
                for line in logs.split("\n"):
                    if line.strip():
                        yield f"data: {line}\n\n"
                        await asyncio.sleep(0.1)
        except Exception as e:
            yield f"data: [ERROR] Failed to stream audit log: {str(e)}\n\n"
        
        yield "data: [COMPLETE] Execution stream finished.\n\n"

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.post("/api/schedule")
def schedule_pipeline(payload: ScheduleRequest):
    """Schedules a pipeline job to run on a background interval."""
    job_id = f"pipeline_job_{int(payload.interval_minutes)}"
    
    # Remove existing job with the same ID if present
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    scheduler.add_job(
        run_end_to_end_pipeline,
        'interval',
        minutes=payload.interval_minutes,
        id=job_id,
        kwargs={"goal": payload.goal, "max_retries": payload.max_retries}
    )
    
    return {
        "status": "SCHEDULED",
        "job_id": job_id,
        "interval_minutes": payload.interval_minutes,
        "goal": payload.goal
    }

@app.get("/api/schedules")
def list_schedules():
    """Lists all active scheduled pipeline background jobs."""
    jobs = scheduler.get_jobs()
    active_jobs = [
        {
            "id": job.id,
            "next_run_time": str(job.next_run_time),
            "trigger": str(job.trigger)
        }
        for job in jobs
    ]
    return {"schedules": active_jobs}

@app.get("/api/logs")
def get_execution_logs():
    """Queries audit log records from pipeline_audit_logs."""
    try:
        logs = get_audit_logs()
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query audit logs: {str(e)}")

@app.delete("/api/schedule/{job_id}")
def delete_schedule(job_id: str):
    """Cancels and removes an active scheduled background job."""
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    
    scheduler.remove_job(job_id)
    return {"status": "DELETED", "job_id": job_id}


if __name__ == "__main__":
    # Neither app.py nor main.py previously called uvicorn.run() anywhere —
    # the only thing that actually started a server was the Dockerfile's
    # `uvicorn app:app` CMD. This lets `python app.py` work too.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
