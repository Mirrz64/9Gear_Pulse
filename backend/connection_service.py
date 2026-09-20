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
    schema = credentials.get("schema")
    if schema is not None and not isinstance(schema, str):
        raise CredentialResolutionError("Postgres schema must be a string if provided.")
    if "database_url" in credentials:
        return
    required = ("host", "database", "username", "password")
    missing = [key for key in required if not credentials.get(key)]
    if missing:
        raise CredentialResolutionError(
            "Postgres credentials require either database_url, or all of "
            f"host/database/username/password - missing: {', '.join(missing)}."
        )


def _validate_auth_method(credentials: dict[str, Any]) -> None:
    """Shared between _validate_api_shape and _validate_graphql_shape -
    both connection types authenticate the exact same two ways (static
    headers, or OAuth2 client-credentials), so this validation logic
    lives in one place rather than being duplicated per type, the same
    principle already applied elsewhere in this module (e.g.
    _split_destination_table's own reasoning about avoiding exactly
    this kind of drift).

    auth_headers is deliberately not required - some public APIs need
    no authentication at all, and requiring one would make those
    impossible to connect. When present, it's a flat header-name ->
    value dict, covering zero, one, or many custom headers (e.g.
    RapidAPI's X-RapidAPI-Key + X-RapidAPI-Host) with no special-casing
    between the single- and multi-header cases.

    oauth2 is deliberately optional and mutually exclusive with
    auth_headers - both exist to answer the same question ("what do I
    send to authenticate"), just two different ways of arriving at an
    Authorization header. Having both set would be ambiguous about
    which one actually wins, so this is rejected outright rather than
    silently picking one.
    """
    auth_headers = credentials.get("auth_headers")
    if auth_headers is not None:
        if not isinstance(auth_headers, dict):
            raise CredentialResolutionError("auth_headers must be an object of header name -> value.")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in auth_headers.items()):
            raise CredentialResolutionError("auth_headers keys and values must all be strings.")
    oauth2 = credentials.get("oauth2")
    if oauth2 is not None:
        if auth_headers:
            raise CredentialResolutionError("A profile can't have both oauth2 and auth_headers set - pick one authentication method.")
        if not isinstance(oauth2, dict):
            raise CredentialResolutionError("oauth2 must be an object if provided.")
        missing = [key for key in ("token_url", "client_id", "client_secret") if not oauth2.get(key)]
        if missing:
            raise CredentialResolutionError(f"oauth2 requires token_url, client_id, and client_secret - missing: {', '.join(missing)}.")
        if not str(oauth2["token_url"]).startswith(("http://", "https://")):
            raise CredentialResolutionError("oauth2.token_url must start with http:// or https://.")
        scope = oauth2.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise CredentialResolutionError("oauth2.scope must be a string if provided.")


def _validate_api_shape(credentials: dict[str, Any]) -> None:
    base_url = credentials.get("base_url")
    if not base_url:
        raise CredentialResolutionError("API credentials require a base_url.")
    if not str(base_url).startswith(("http://", "https://")):
        raise CredentialResolutionError("base_url must start with http:// or https://.")
    _validate_auth_method(credentials)
    # method is deliberately optional, defaulting to GET at resolution
    # time (see api_credentials()) rather than required here - every
    # profile created before this field existed has none stored at all,
    # and GET is what they've always actually done.
    method = credentials.get("method")
    if method is not None and str(method).upper() not in ("GET", "POST"):
        raise CredentialResolutionError("API method must be GET or POST if provided.")
    # request_body only makes sense for POST, but not cross-validated
    # against method here - a GET with a body is unusual but not
    # actually forbidden by HTTP, and this validator checks shape, not
    # intent, matching every other validator in this module.
    request_body = credentials.get("request_body")
    if request_body is not None and not isinstance(request_body, dict):
        raise CredentialResolutionError("request_body must be a JSON object if provided.")
    # pagination is deliberately optional and, when absent, means
    # exactly what it always has - a single response, no pagination at
    # all. Every profile created before this field existed has none
    # stored, and that's correctly unchanged behavior, not a gap.
    pagination = credentials.get("pagination")
    if pagination is not None:
        if not isinstance(pagination, dict):
            raise CredentialResolutionError("pagination must be an object if provided.")
        style = pagination.get("style")
        if style not in ("offset", "page", "cursor"):
            raise CredentialResolutionError("pagination.style must be one of: offset, page, cursor.")
        if style == "offset":
            if not pagination.get("offset_param") or not pagination.get("limit_param"):
                raise CredentialResolutionError("offset-style pagination requires offset_param and limit_param.")
        elif style == "page":
            if not pagination.get("page_param"):
                raise CredentialResolutionError("page-style pagination requires page_param.")
        elif style == "cursor":
            if not pagination.get("cursor_param") or not pagination.get("next_cursor_field"):
                raise CredentialResolutionError("cursor-style pagination requires cursor_param and next_cursor_field.")
        page_size = pagination.get("page_size")
        if page_size is not None and (not isinstance(page_size, int) or page_size < 1):
            raise CredentialResolutionError("pagination.page_size must be a positive integer if provided.")


def _validate_graphql_shape(credentials: dict[str, Any]) -> None:
    """GraphQL always POSTs a {query, variables} body to a single
    endpoint - there's no separate "method" or "pagination" concept the
    way REST has (Relay-style cursor pagination, where an API supports
    it, is expressed IN the query/variables themselves, not as a
    separate connection-profile setting), so this validator is
    genuinely simpler than _validate_api_shape despite sharing its auth
    handling via _validate_auth_method.
    """
    endpoint = credentials.get("endpoint")
    if not endpoint:
        raise CredentialResolutionError("GraphQL credentials require an endpoint.")
    if not str(endpoint).startswith(("http://", "https://")):
        raise CredentialResolutionError("endpoint must start with http:// or https://.")
    query = credentials.get("query")
    if not query or not isinstance(query, str):
        raise CredentialResolutionError("GraphQL credentials require a query string.")
    variables = credentials.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise CredentialResolutionError("variables must be a JSON object if provided.")
    _validate_auth_method(credentials)


def _validate_soap_shape(credentials: dict[str, Any]) -> None:
    """SOAP always POSTs a literal, full XML envelope to a single
    endpoint - the request_body here is the exact XML text to send
    (constructing a valid envelope requires knowing the WSDL's own
    XSD-defined shape, genuinely separate scope from validating a
    connection profile), analogous to GraphQL's own raw query string.

    soap_version matters because SOAP 1.1 and 1.2 identify the
    operation being called differently at the HTTP level - 1.1 uses a
    separate SOAPAction header, 1.2 embeds the action inside the
    Content-Type header itself. Confirmed directly against real,
    documented examples of both before writing this - never assumed.
    """
    endpoint = credentials.get("endpoint")
    if not endpoint:
        raise CredentialResolutionError("SOAP credentials require an endpoint.")
    if not str(endpoint).startswith(("http://", "https://")):
        raise CredentialResolutionError("endpoint must start with http:// or https://.")
    soap_version = credentials.get("soap_version")
    if soap_version not in ("1.1", "1.2"):
        raise CredentialResolutionError("soap_version must be '1.1' or '1.2'.")
    soap_action = credentials.get("soap_action")
    if soap_action is not None and not isinstance(soap_action, str):
        raise CredentialResolutionError("soap_action must be a string if provided.")
    request_body = credentials.get("request_body")
    if not request_body or not isinstance(request_body, str):
        raise CredentialResolutionError("SOAP credentials require a request_body (the full XML envelope to send).")
    _validate_auth_method(credentials)


def _validate_file_shape(credentials: dict[str, Any]) -> None:
    # File-upload profiles don't store real connection info in
    # encrypted_credentials at all - the actual per-file data lives in
    # the connection_profile_files relationship (see file_credentials()
    # below and ConnectionProfileFile in models.py). This validator
    # exists purely so validate_credentials_shape()'s dispatch stays
    # uniform across every connection type; there's nothing to actually
    # check on the credentials dict itself.
    pass


def _validate_redis_shape(credentials: dict[str, Any]) -> None:
    # Unlike Postgres, Redis has no separate "database name" - db is
    # just a numbered logical index (0-15 by default), optional,
    # defaulting to 0 if never set. Many Redis instances (especially in
    # dev) run with no password at all, so password/username are
    # deliberately optional here too - only host is genuinely required
    # when not using the redis_url escape hatch.
    db = credentials.get("db")
    if db is not None and not isinstance(db, int):
        raise CredentialResolutionError("Redis db must be an integer if provided.")
    if "redis_url" in credentials:
        return
    if not credentials.get("host"):
        raise CredentialResolutionError(
            "Redis credentials require either redis_url, or at minimum a host "
            "(port defaults to 6379, username/password/db are all optional)."
        )


def validate_credentials_shape(connection_type: ConnectionType, credentials: dict[str, Any]) -> None:
    """Checked once at connection-profile creation time, so a malformed
    credentials payload fails immediately and clearly instead of only
    surfacing later, the first time someone tries to introspect or
    generate against it.
    """
    if connection_type == ConnectionType.postgres:
        _validate_postgres_shape(credentials)
    elif connection_type == ConnectionType.api:
        _validate_api_shape(credentials)
    elif connection_type == ConnectionType.file:
        _validate_file_shape(credentials)
    elif connection_type == ConnectionType.redis:
        _validate_redis_shape(credentials)
    elif connection_type == ConnectionType.graphql:
        _validate_graphql_shape(credentials)
    elif connection_type == ConnectionType.soap:
        _validate_soap_shape(credentials)
    else:
        raise CredentialResolutionError(
            f"Only Postgres, API, file, Redis, GraphQL, and SOAP connection profiles are supported in v1 (got '{connection_type.value}')."
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


def postgres_schema_name(profile: ConnectionProfile) -> str:
    """Resolves which schema a Postgres source or destination should be
    introspected/read from. Defaults to "public" so every profile
    created before this field existed keeps behaving exactly as it did
    - introspect_schema() itself already defaulted to "public" when no
    schema_name was ever passed, so an absent/None schema here preserves
    that exact prior behavior rather than changing it retroactively.
    """
    credentials = decrypt_credentials(profile)
    return credentials.get("schema") or "public"


def api_credentials(profile: ConnectionProfile) -> dict[str, Any]:
    """Analogous to postgres_url() but for API sources - returns the
    resolved connection details as a dict rather than a single URL
    string, since an API source is a base_url plus zero or more auth
    headers (plus, now, an optional method and request body), not one
    connection string the way a database is.

    auth_headers is a flat header-name -> value dict, stored verbatim
    per-profile. This covers a no-auth public API (empty dict), a
    single-header API ({"Authorization": "Bearer ..."}), and a
    multi-header API ({"X-RapidAPI-Key": "...", "X-RapidAPI-Host": "..."})
    the same way, with no special-casing between them.

    method defaults to "GET" - every profile created before this field
    existed has none stored, and GET is exactly what they've always
    done; this preserves that behavior unchanged rather than requiring
    every existing profile to be edited. request_body defaults to an
    empty dict, meaningful only when method is "POST". pagination
    defaults to None - absent means no pagination, a single response,
    exactly today's original behavior for every profile that predates
    this field.

    oauth2 defaults to None and, when present, is returned as the RAW
    config (token_url/client_id/client_secret/scope) - deliberately
    NEVER a pre-fetched access token. A token fetched here, at profile-
    resolution time, could easily be expired by the time a sandbox
    actually runs (self-healing retries alone can span several
    minutes) - the token needs fetching, and refreshing on expiry, at
    the point actual requests happen, which for generated pipeline code
    means inside the sandbox at real execution time, not here. See
    introspect_api()'s own oauth2 handling for the one case that IS
    different: introspection makes its one sample request immediately,
    so it genuinely does need a real, freshly-fetched token right now.
    """
    credentials = decrypt_credentials(profile)
    base_url = credentials.get("base_url")
    if not base_url:
        raise CredentialResolutionError("API credentials require a base_url.")
    return {
        "base_url": base_url,
        "auth_headers": credentials.get("auth_headers") or {},
        "method": str(credentials.get("method") or "GET").upper(),
        "request_body": credentials.get("request_body") or {},
        "pagination": credentials.get("pagination"),
        "oauth2": credentials.get("oauth2"),
    }


def file_credentials(profile: ConnectionProfile) -> list[dict[str, Any]]:
    """Analogous to postgres_url()/api_credentials() but for file-upload
    sources - returns the list of {storage_path, original_filename,
    format} dicts that introspect_files() and sandbox staging both need.

    Unlike the other two, this doesn't touch encrypted_credentials or
    decrypt_credentials() at all - a file profile's real connection
    info lives in the connection_profile_files relationship, not the
    encrypted blob (which just holds a placeholder for this type - see
    ConnectionProfileFile's docstring in models.py). No decryption step
    means no corrupt-blob failure mode here; the only real failure mode
    is an empty file list, which is still worth surfacing clearly
    rather than silently introspecting nothing.
    """
    if not profile.files:
        raise CredentialResolutionError("This file connection profile has no uploaded files.")
    return [
        {
            "storage_path": f.storage_path,
            "original_filename": f.original_filename,
            "format": f.format,
        }
        for f in profile.files
    ]


def redis_url(profile: ConnectionProfile) -> str:
    """Analogous to postgres_url() - a single connection string, since
    redis-py (like SQLAlchemy) accepts one directly via
    Redis.from_url(). Same deliberate choice as postgres_url(): no
    URL-encoding of username/password, for consistency with that
    existing function rather than fixing the encoding gap in one
    resolver and not the other - a password containing "@" or ":"
    would break either one identically, a known, pre-existing
    limitation this doesn't newly introduce.
    """
    credentials = decrypt_credentials(profile)
    if "redis_url" in credentials:
        return str(credentials["redis_url"])
    host = credentials.get("host")
    if not host:
        raise CredentialResolutionError("Redis credentials require redis_url or at minimum a host.")
    port = credentials.get("port", 6379)
    db = credentials.get("db", 0)
    username = credentials.get("username")
    password = credentials.get("password")
    if password:
        auth = f"{username or ''}:{password}@"
    elif username:
        auth = f"{username}@"
    else:
        auth = ""
    scheme = "rediss" if credentials.get("tls") else "redis"
    return f"{scheme}://{auth}{host}:{port}/{db}"


def graphql_credentials(profile: ConnectionProfile) -> dict[str, Any]:
    """Analogous to api_credentials() - a GraphQL source is always POST
    to a single endpoint with {query, variables}, never GET, never
    multiple endpoints, so there's no method or pagination concept to
    resolve the way REST has. auth_headers/oauth2 are returned with the
    exact same shape and field names api_credentials() already uses,
    deliberately - generated code (and the sandbox env vars that carry
    this through) reuse the SOURCE_API_AUTH_HEADERS/SOURCE_API_OAUTH_*
    pattern verbatim rather than inventing GraphQL-specific auth env
    vars for a mechanism that's genuinely identical either way.
    """
    credentials = decrypt_credentials(profile)
    endpoint = credentials.get("endpoint")
    if not endpoint:
        raise CredentialResolutionError("GraphQL credentials require an endpoint.")
    query = credentials.get("query")
    if not query:
        raise CredentialResolutionError("GraphQL credentials require a query string.")
    return {
        "endpoint": endpoint,
        "query": query,
        "variables": credentials.get("variables") or {},
        "auth_headers": credentials.get("auth_headers") or {},
        "oauth2": credentials.get("oauth2"),
    }


def soap_credentials(profile: ConnectionProfile) -> dict[str, Any]:
    """Analogous to graphql_credentials() - a SOAP source always POSTs
    a literal, full XML envelope (request_body) to a single endpoint.
    soap_action defaults to an empty string rather than None, since
    generated code builds the SOAPAction header unconditionally for
    SOAP 1.1 (an empty SOAPAction header is valid and simply means "no
    specific action"), and this keeps that code from needing a None
    check on top of the version branch it already needs.
    """
    credentials = decrypt_credentials(profile)
    endpoint = credentials.get("endpoint")
    if not endpoint:
        raise CredentialResolutionError("SOAP credentials require an endpoint.")
    request_body = credentials.get("request_body")
    if not request_body:
        raise CredentialResolutionError("SOAP credentials require a request_body.")
    return {
        "endpoint": endpoint,
        "soap_version": credentials.get("soap_version") or "1.1",
        "soap_action": credentials.get("soap_action") or "",
        "request_body": request_body,
        "auth_headers": credentials.get("auth_headers") or {},
        "oauth2": credentials.get("oauth2"),
    }
