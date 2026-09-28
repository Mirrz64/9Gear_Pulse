"""
9Gear Pulse - self-healing ledger.

Every healing attempt used to be discarded the moment it finished: only
the final code survived, so the error, the AI's diagnosis, what it
changed, and whether the change actually worked were all lost. That is
exactly the data needed to (a) measure how good self-healing really is,
and (b) train a smaller model on routine fixes later - and it can't be
regenerated after the fact, which is why capture starts now.

Three rules this module is built around:

1. Recording NEVER breaks a real run. Persistence happens inside a
   SAVEPOINT and any failure is swallowed and printed - a ledger bug
   must not turn a successful pipeline test into a failed one.
2. Logs are redacted before storage (best-effort - see redact_log()).
   Stored rows can contain fragments of a user's own schema or error
   output, so they are also deleted with the project/pipeline that
   produced them (ON DELETE CASCADE on the table).
3. Storing is not the same as training on it. Using any of this to
   train a model needs its own explicit consent decision - this module
   only captures and summarizes.
"""
import re
from typing import Any, Optional

MAX_ERROR_LOG_CHARS = 20_000
MAX_DETAIL_CHARS = 2_000

_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
# scheme://user:password@host  ->  scheme://user:***@host
_URL_CREDS_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s@]+:)([^@\s/]+)(@)")
# password=..., "api_key": "...", token: ... and similar key/value leaks
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|account[_-]?key|private[_-]?key)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s\"',;&)]+)"
)


def redact_log(text: Optional[str]) -> Optional[str]:
    """Best-effort masking of credentials in log-like text: private-key
    blocks, passwords embedded in connection URLs, and key=value style
    secrets. Genuinely best-effort - a secret in an unrecognized shape
    passes through - which is why credentials are also never meant to
    appear in logs at all (generated code reads them from env vars).
    """
    if text is None:
        return None
    text = _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _URL_CREDS_RE.sub(r"\1***\3", text)
    text = _KV_SECRET_RE.sub(r"\1\2***", text)
    return text


def redact_code(text: Optional[str]) -> Optional[str]:
    """Code gets ONLY private-key blocks masked. The log patterns above
    would corrupt real code - e.g. `password = os.environ["X"]` or an
    f-string URL like f"postgresql://{user}:{pw}@{host}" both look like
    leaks to a regex but are ordinary, correct code - and corrupted
    code is worthless as a record of what a fix actually looked like.
    Generated code reads credentials from env vars, so real secrets
    shouldn't be in it to begin with.
    """
    if text is None:
        return None
    return _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)


def _clip(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + f"... [truncated, {len(text)} chars total]"


def _enum_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _event_to_row(event: dict, *, project_id, pipeline_id, version_id, file_id, source_type, destination_type):
    from models import HealingEvent

    summary = event.get("error_summary") or None
    exception_type = (summary or {}).get("exception_type")
    return HealingEvent(
        project_id=project_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=version_id,
        pipeline_version_file_id=file_id,
        trigger=str(event.get("trigger") or "execution_error")[:32],
        attempt_number=int(event.get("attempt_number") or 1),
        outcome=str(event.get("outcome") or "unknown")[:32],
        provider=event.get("provider"),
        model=event.get("model"),
        source_type=_enum_str(source_type),
        destination_type=_enum_str(destination_type),
        exception_type=exception_type[:200] if exception_type else None,
        error_summary=summary,
        error_log=_clip(redact_log(event.get("error_log")), MAX_ERROR_LOG_CHARS),
        code_before=redact_code(event.get("code_before")),
        code_after=redact_code(event.get("code_after")),
        root_cause=event.get("root_cause"),
        changes_made=event.get("changes_made"),
        same_error=event.get("same_error"),
        detail=_clip(redact_log(event.get("detail")), MAX_DETAIL_CHARS),
    )


def record_healing_events(db, events: list, *, project_id, pipeline_id, version_id, file_id, source_type, destination_type) -> None:
    """Persists collected events. Called by the two generation tasks just
    before their own commit. Never raises - see rule 1 in the module
    docstring. add_all runs inside a SAVEPOINT (begin_nested flushes any
    already-pending state first), so if a ledger insert fails only the
    ledger rows roll back, never the pipeline run being recorded beside
    them.
    """
    if not events:
        return
    try:
        rows = [
            _event_to_row(
                e, project_id=project_id, pipeline_id=pipeline_id, version_id=version_id,
                file_id=file_id, source_type=source_type, destination_type=destination_type,
            )
            for e in events
        ]
        with db.begin_nested():
            db.add_all(rows)
    except Exception as exc:
        print(f"[Healing Ledger] Could not record {len(events)} healing event(s): {exc}")


# Outcomes where the AI actually produced a fix that was then tested.
# heal_call_failed is excluded from fix-rate math: no fix ever existed
# to succeed or fail, so counting it would blame the fixer for an
# outage or a malformed response. recheck_failed (the fix ran, but the
# follow-up quality check itself crashed) is excluded for the same
# reason: there is no verdict on the fix to count either way.
_FIXED = {"fixed"}
_ATTEMPTED_FAILURES = {"still_failing", "broke_execution", "warnings_persist"}


def _summarize(rows: list) -> dict:
    total = len(rows)
    fixed = sum(1 for r in rows if r["outcome"] in _FIXED)
    attempted = fixed + sum(1 for r in rows if r["outcome"] in _ATTEMPTED_FAILURES)
    return {"total": total, "fixed": fixed, "fix_rate": round(fixed / attempted, 3) if attempted else None}


def _group(rows: list, key_fn) -> list:
    buckets: dict = {}
    for r in rows:
        buckets.setdefault(key_fn(r), []).append(r)
    return [{"key": k, **_summarize(v)} for k, v in buckets.items()]


def aggregate_stats(rows: list) -> dict:
    """rows: dicts with trigger, attempt_number, model, exception_type,
    outcome. Pure function, so it's tested without a database.

    fix_rate is fixed / (fixed + fixes that were tested and failed) -
    None when nothing has been attempted yet, rather than a misleading 0.
    """
    outcomes: dict = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1

    by_attempt = sorted(
        ({"attempt_number": g["key"], **{k: v for k, v in g.items() if k != "key"}} for g in _group(rows, lambda r: r["attempt_number"])),
        key=lambda g: g["attempt_number"],
    )
    by_model = sorted(
        ({"model": g["key"] or "unknown", **{k: v for k, v in g.items() if k != "key"}} for g in _group(rows, lambda r: r["model"])),
        key=lambda g: -g["total"],
    )
    by_trigger = sorted(
        ({"trigger": g["key"], **{k: v for k, v in g.items() if k != "key"}} for g in _group(rows, lambda r: r["trigger"])),
        key=lambda g: -g["total"],
    )
    top_errors = sorted(
        ({"exception_type": g["key"] or "unparsed", **{k: v for k, v in g.items() if k != "key"}} for g in _group(rows, lambda r: r["exception_type"])),
        key=lambda g: -g["total"],
    )[:10]

    return {
        "total_events": len(rows),
        "outcomes": outcomes,
        **{k: v for k, v in _summarize(rows).items() if k in ("fixed", "fix_rate")},
        "by_attempt": by_attempt,
        "by_model": by_model,
        "by_trigger": by_trigger,
        "top_errors": top_errors,
    }


def get_healing_stats(db, owner_id) -> dict:
    """Stats over the calling user's OWN projects only - ownership is
    enforced by the join to Project.owner_id, the same scoping every
    other endpoint uses, so one user never sees another's ledger.
    """
    from sqlalchemy import select
    from models import HealingEvent, Project

    stmt = (
        select(
            HealingEvent.trigger, HealingEvent.attempt_number, HealingEvent.model,
            HealingEvent.exception_type, HealingEvent.outcome,
        )
        .join(Project, Project.id == HealingEvent.project_id)
        .where(Project.owner_id == owner_id)
    )
    return aggregate_stats([dict(r._mapping) for r in db.execute(stmt)])
