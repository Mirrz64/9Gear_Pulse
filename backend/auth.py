"""
Resolves a verified Clerk session into our own User row.

This is the piece actor_id should have been all along - instead of a
bare UUID the client supplies (and could supply *any* value for), every
protected endpoint should depend on get_current_user, which:
  1. Verifies the incoming Authorization: Bearer <token> against Clerk
     (clerk_backend_api.authenticate_request - the same SDK already used
     for the webhook's user sync).
  2. Extracts the Clerk user ID from the verified token's `sub` claim.
  3. Looks up the matching User row via auth_provider_id - the same
     field clerk_webhooks.py populates.

Raises 401 on anything that doesn't check out - no token, an invalid/
expired token, or a valid token for a Clerk user with no corresponding
local row yet (shouldn't happen once the webhook has run, but this
fails safely rather than silently creating one here).
"""
import os
import logging

from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions

from session import get_db
from models import User

logger = logging.getLogger("auth")

CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY")

# The origin(s) a valid session token is allowed to have come from -
# matches the same origins already allowed in app.py's CORS config.
# Comma-separated via env var so this doesn't need a code change to add
# a production origin later.
_AUTHORIZED_PARTIES = os.environ.get(
    "CLERK_AUTHORIZED_PARTIES", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if not CLERK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY is not configured")

    with Clerk(bearer_auth=CLERK_SECRET_KEY) as clerk:
        request_state = clerk.authenticate_request(
            request,
            AuthenticateRequestOptions(authorized_parties=_AUTHORIZED_PARTIES),
        )

    if not request_state.is_signed_in:
        raise HTTPException(status_code=401, detail=f"Not signed in: {request_state.reason}")

    payload = request_state.payload
    # Defensive against payload being either dict-like or object-like -
    # the exact shape isn't something to assume without seeing it, same
    # lesson as the svix .verify() surprise a few minutes ago.
    clerk_user_id = payload.get("sub") if isinstance(payload, dict) else getattr(payload, "sub", None)
    if not clerk_user_id:
        raise HTTPException(status_code=401, detail="Session token missing subject claim")

    user = db.scalar(select(User).where(User.auth_provider_id == clerk_user_id))
    if not user:
        raise HTTPException(
            status_code=401,
            detail="No local account found for this Clerk user - the webhook sync may not have completed yet",
        )

    return user
