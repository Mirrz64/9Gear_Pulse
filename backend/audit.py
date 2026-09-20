"""Shared audit-log helper.

Extracted out of review_api.py specifically so generation_tasks.py can
record audit entries too, without creating a circular import: review_api.py
must import generation_tasks.py (to dispatch its Celery tasks), so
generation_tasks.py importing back from review_api.py would cycle.
Neither module imports from the other here - both import from this one.

Kept as the exact same name and signature review_api.py already used
in dozens of call sites, so adopting this module there is a one-line
change (replace the local def with this import) rather than touching
every call site.
"""
import uuid

from sqlalchemy.orm import Session

from models import AuditLog


def _audit(db: Session, actor_id: uuid.UUID, action: str, entity_type: str, entity_id: uuid.UUID) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id))
