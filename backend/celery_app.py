"""Celery application instance for 9Gear Pulse's background job queue.

Redis-backed, chosen over a Postgres-native queue specifically because
the highest-volume workload this queue needs to absorb at real
commercial scale isn't the human-gated build/review flow (generate,
test, review a file) - it's scheduled, recurring pipeline EXECUTION
(schedule_service.py's run_pinned_version), which runs completely
automatically, with no human involved, and scales with
(customers) x (schedules per customer) x (frequency). That's the
workload Redis has the proven ceiling for.

This is deliberately separate from - and unrelated to - Redis as a
CONNECTION TYPE users can point their own pipelines at. That's a
different Redis instance entirely, serving a different purpose, never
to be confused with this one.

Run a worker locally with:
    celery -A celery_app worker --loglevel=info
This needs its own terminal, staying alive alongside uvicorn, the
Clerk webhook relay, and the frontend dev server - a fourth
long-running local process, not optional infrastructure.
"""
import os

from celery import Celery

# Two separate logical Redis databases on the same instance - broker
# traffic (task messages) and result-backend traffic (task state/return
# values) are kept apart by convention, not because either strictly
# requires it, but so one workload's queue depth is never confused with
# the other's when inspecting Redis directly.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
BROKER_URL = f"{REDIS_URL}/0"
RESULT_BACKEND_URL = f"{REDIS_URL}/1"

celery_app = Celery(
    "ninegear_pulse",
    broker=BROKER_URL,
    backend=RESULT_BACKEND_URL,
    include=["generation_tasks"],
)

celery_app.conf.update(
    # JSON only, never pickle - pickle can execute arbitrary code on
    # deserialization, an unnecessary risk for a queue whose messages
    # are just a handful of UUIDs and strings.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # A task's own return value isn't actually used anywhere right now
    # (the task writes its result straight to Postgres, which is what
    # the frontend polls) - but keeping results for a short window is
    # cheap and useful for debugging a stuck or failed task directly
    # from Redis without needing to reproduce it.
    result_expires=3600,
    # A generation+sandbox task can legitimately run for minutes (AI
    # calls, multiple self-healing sandbox attempts) - acknowledge the
    # task only after it actually finishes, not the moment a worker
    # picks it up, so a worker crashing mid-task doesn't silently lose
    # the job. The trade-off (a crashed worker's in-flight task can be
    # redelivered and run twice) is the right one here: the endpoint
    # that dispatches these tasks already flips generation_in_progress
    # to True and commits before dispatch, and re-running a generation
    # task is safe - it just overwrites the same row with a fresh
    # attempt, never worse than losing the job outright.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
