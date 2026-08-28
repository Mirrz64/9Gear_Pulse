"""
Clerk webhook endpoint - syncs Clerk users into our own `users` table.

Populates User.auth_provider_id with Clerk's user ID (e.g. "user_xxx"),
matching the field's original intent (see models.py). This is the piece
that field was always waiting on - no schema change needed here.

Signature verification requires the RAW request body, not a parsed
Pydantic model - re-serializing parsed JSON produces different bytes
than what Clerk actually signed, which silently breaks verification.
That's why this reads `await request.body()` directly rather than
declaring a request body type on the route.
"""
import os
import logging

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError
from sqlalchemy import select

from session import get_db
from models import User

logger = logging.getLogger("clerk_webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["auth"])

WEBHOOK_SIGNING_SECRET = os.environ.get("CLERK_WEBHOOK_SIGNING_SECRET")


def _primary_email(user_data: dict) -> str | None:
    """Extract the primary email from a Clerk user object, not just the first one."""
    addresses = user_data.get("email_addresses") or []
    primary_id = user_data.get("primary_email_address_id")
    for addr in addresses:
        if addr.get("id") == primary_id:
            return addr.get("email_address")
    # Fall back to the first address if no primary is marked (shouldn't
    # normally happen, but better than dropping the user entirely).
    return addresses[0].get("email_address") if addresses else None


@router.post("/clerk")
async def clerk_webhook(request: Request, db: Session = Depends(get_db)):
    if not WEBHOOK_SIGNING_SECRET:
        # Fails loudly rather than silently accepting unverifiable events -
        # this should only ever happen if the webhook endpoint was
        # registered in Clerk before CLERK_WEBHOOK_SIGNING_SECRET was set
        # locally.
        raise HTTPException(status_code=500, detail="CLERK_WEBHOOK_SIGNING_SECRET is not configured")

    payload = await request.body()
    try:
        wh = Webhook(WEBHOOK_SIGNING_SECRET)
        event = wh.verify(payload, dict(request.headers))
        # Temporary diagnostic - .verify() is documented to return the
        # parsed payload on success, but it's coming back None here,
        # which the docs don't describe. Logging the real type/value
        # rather than guessing at a fix blind.
        logger.warning(f"[DIAGNOSTIC] wh.verify() returned: type={type(event)!r} value={event!r}")
    except WebhookVerificationError as e:
        logger.warning(f"[DIAGNOSTIC] WebhookVerificationError: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event is None:
        # Matches the documented Svix contract (verify-only, raises on
        # failure) rather than assuming a return value that isn't
        # actually there - parse the already-verified raw payload
        # ourselves.
        import json
        event = json.loads(payload)

    event_type = event.get("type")
    data = event.get("data", {})

    if event_type in ("user.created", "user.updated"):
        clerk_user_id = data.get("id")
        email = _primary_email(data)
        if not clerk_user_id or not email:
            logger.warning(f"Skipping {event_type}: missing id or email in payload")
            return {"status": "skipped"}

        existing = db.scalar(select(User).where(User.auth_provider_id == clerk_user_id))
        if existing:
            existing.email = email
        else:
            # Also idempotent against re-signup with the same email
            # under a different Clerk instance/environment.
            by_email = db.scalar(select(User).where(User.email == email))
            if by_email:
                by_email.auth_provider_id = clerk_user_id
            else:
                db.add(User(auth_provider_id=clerk_user_id, email=email))
        db.commit()

    elif event_type == "user.deleted":
        # Deliberately not deleting the local User row here - projects,
        # connection_profiles, and pipelines all cascade from owner_id,
        # so a silent delete here would be destructive in a way that's
        # hard to undo. Logged for now; a real deactivation policy is a
        # separate decision to make explicitly, not a side effect of a
        # webhook handler.
        logger.info(f"Clerk user.deleted received for {data.get('id')} - no local action taken")

    return {"status": "ok"}
