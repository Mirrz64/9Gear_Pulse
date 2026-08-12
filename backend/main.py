import os
import time
import argparse
from dotenv import load_dotenv

from introspect import introspect_schema
from generate_pipeline import generate_pipeline
from heal_pipeline import execute_with_self_healing
from logger import log_pipeline_run

load_dotenv(override=True)

def run_end_to_end_pipeline(goal: str, output_path: str = "generated_pipeline.py", max_retries: int = 3):
    """Orchestrates schema introspection, code generation, sandbox testing with self-healing,
    and audit logging.
    """
    start_time = time.time()
    print("==================================================")
    print("      9GEAR PULSE - PIPELINE ORCHESTRATOR         ")
    print("==================================================")
    print(f"[1/4] Introspecting database schema...")
    
    try:
        schema_summary = introspect_schema()
        print("      Schema introspection complete.")
    except Exception as e:
        error_msg = f"Schema introspection failed: {str(e)}"
        print(f"[Error]: {error_msg}")
        log_pipeline_run("orchestrator_init", "FAILED", 0, error_msg, error_msg)
        return False

    print(f"\n[2/4] Generating dlt pipeline code for goal:\n      \"{goal}\"...")
    try:
        pipeline_data = generate_pipeline(schema_summary, goal)
        pipeline_name = pipeline_data.get("pipeline_name", "generated_pipeline")
        code = pipeline_data.get("code")

        if not code:
            raise ValueError("Model generated response without executable code.")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        print(f"      Successfully generated '{pipeline_name}' -> Saved to {output_path}")
    except Exception as e:
        error_msg = f"Pipeline generation failed: {str(e)}"
        print(f"[Error]: {error_msg}")
        log_pipeline_run("generator", "FAILED", 0, error_msg, error_msg)
        return False

    print(f"\n[3/4] Executing pipeline in Docker sandbox (Self-Healing Enabled, Max Retries: {max_retries})...")
    success, attempts, final_logs = execute_with_self_healing(
        script_path=output_path, 
        schema_summary=schema_summary, 
        max_retries=max_retries
    )

    elapsed_time = round(time.time() - start_time, 2)
    status = "SUCCESS" if success else "FAILED"

    print(f"\n[4/4] Writing run metrics to database audit logs...")
    log_pipeline_run(
        pipeline_name=pipeline_name,
        status=status,
        attempts=attempts,
        logs=final_logs,
        error_summary=None if success else final_logs[-500:]  # Capture trailing error context
    )

    print("==================================================")
    print(f" Execution Summary: Status={status} | Attempts={attempts} | Duration={elapsed_time}s")
    print("==================================================")
    
    return success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="9Gear Pulse Pipeline Engine")
    parser.add_argument("--goal", type=str, help="Plain English data pipeline goal")
    args = parser.parse_args()

    user_goal = args.goal
    if not user_goal:
        user_goal = input("\nDescribe the pipeline you want to generate (plain English): ")

    run_end_to_end_pipeline(goal=user_goal)