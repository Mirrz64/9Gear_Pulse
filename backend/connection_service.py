"""Credential decryption and connection URL construction for backend-only use.

Raises CredentialResolutionError, not HTTPException, throughout. This
module is called from two very different contexts: live HTTP requests
(review_api.py) and APScheduler background jobs (schedule_service.py,
outside any request/response cycle). HTTPException means nothing in the
second context - it doesn't get translated into an HTTP response, it
just propagates as an unhandled exception into APScheduler's own error
handling and gets silently swallowed, so a scheduled run's credential
failure would vanish with no PipelineRun and no audit trail at all.

Callers translate CredentialResolutionError into whatever's appropriate
for their context: review_api.py catches it and raises HTTPException;
schedule_service.py catches it and records a failed PipelineRun instead.
"""
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from models import ConnectionProfile, ConnectionType


class CredentialResolutionError(Exception):
    """Credentials couldn't be decrypted, validated, or turned into a
    usable connection string. Deliberately not an HTTPException - see
    the module docstring for why.
    """


def credential_cipher() -> Fernet:
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise CredentialResolutionError(
            "CREDENTIAL_ENCRYPTION_KEY is not configured; credentials cannot be stored or read safely."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialResolutionError("CREDENTIAL_ENCRYPTION_KEY is invalid.") from exc


def decrypt_credentials(profile: ConnectionProfile) -> dict[str, Any]:
    try:
        return json.loads(credential_cipher().decrypt(profile.encrypted_credentials).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialResolutionError("Stored connection credentials cannot be decrypted.") from exc


def _validate_postgres_shape(credentials: dict[str, Any]) -> None:
    if "database_url" in credentials:
        return
    required = ("host", "database", "username", "password")
    missing = [key for key in required if not credentials.get(key)]
    if missing:
        raise CredentialResolutionError(
            "Postgres credentials require either database_url, or all of "
            f"host/database/username/password - missing: {', '.join(missing)}."
        )


def validate_credentials_shape(connection_type: ConnectionType, credentials: dict[str, Any]) -> None:
    """Checked once at connection-profile creation time, so a malformed
    credentials payload fails immediately and clearly instead of only
    surfacing later, the first time someone tries to introspect or
    generate against it.
    """
    if connection_type == ConnectionType.postgres:
        _validate_postgres_shape(credentials)
    else:
        raise CredentialResolutionError(
            f"Only Postgres connection profiles are supported in v1 (got '{connection_type.value}')."
        )


def postgres_url(profile: ConnectionProfile) -> str:
    credentials = decrypt_credentials(profile)
    if "database_url" in credentials:
        return str(credentials["database_url"])
    required = ("host", "database", "username", "password")
    if any(not credentials.get(key) for key in required):
        raise CredentialResolutionError(
            "Postgres credentials require database_url or host, database, username, and password."
        )
    port = credentials.get("port", 5432)
    return f"postgresql://{credentials['username']}:{credentials['password']}@{credentials['host']}:{port}/{credentials['database']}"
