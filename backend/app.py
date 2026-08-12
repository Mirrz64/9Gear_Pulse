import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from main import run_end_to_end_pipeline
from introspect import introspect_schema

app = FastAPI(
    title="9Gear Pulse API",
    description="Control plane for AI-generated ETL pipelines and execution auditing",
    version="1.0.0"
)

# Allow CORS for Next.js web frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PipelineRequest(BaseModel):
    goal: str
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

@app.post("/api/run")
def trigger_pipeline(payload: PipelineRequest):
    """Triggers schema introspection, pipeline generation, Docker sandboxing, and audit logging."""
    success = run_end_to_end_pipeline(goal=payload.goal, max_retries=payload.max_retries)
    if not success:
        raise HTTPException(status_code=500, detail="Pipeline execution or self-healing failed.")
    return {"status": "SUCCESS", "goal": payload.goal}

@app.get("/api/logs")
def get_execution_logs():
    """Queries audit log records from PostgreSQL."""
    dest_db_url = os.environ.get("DEST_DB_URL")
    if not dest_db_url:
        raise HTTPException(status_code=500, detail="DEST_DB_URL not configured")

    engine = create_engine(dest_db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT id, pipeline_name, status, attempts, execution_time, logs "
                     "FROM _pipeline_audit.execution_logs ORDER BY id DESC LIMIT 20;")
            )
            logs = [dict(row._mapping) for row in result]
            return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query audit logs: {str(e)}")