"""
Schema introspection for 9Gear Pulse — Phase 1 of the AI-to-pipeline engine.

Connects using environment variables or DATABASE_URL/SOURCE_DB_URL.
Returns a compact JSON summary of tables/columns/types/row counts/sample rows.
"""
import datetime
import os
import re
from typing import Optional
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

# Compose supplies an in-network DATABASE_URL. A host-only URL in .env must
# not replace it once this module is running inside the backend container.
load_dotenv(override=False)


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)


def get_db_url() -> str:
    """Resolves the database URL from environment variables, handling hostnames,

    Postgres parameters, and SQLite fallbacks seamlessly.
    """
    # 1. Check direct URL strings
    url = os.getenv("SOURCE_DB_URL") or os.getenv("DATABASE_URL")
    if url:
        # If running locally outside docker, translate container host '9gear_pulse_db' to localhost
        if "9gear_pulse_db" in url and not os.getenv("RUNNING_IN_DOCKER"):
            url = url.replace("9gear_pulse_db", "localhost")
        return url

    # 2. Check individual Postgres ENV vars
    pg_host = os.getenv("PG_HOST", "localhost")
    if pg_host == "9gear_pulse_db" and not os.getenv("RUNNING_IN_DOCKER"):
        pg_host = "localhost"

    pg_user = os.getenv("PG_USER", "pulse")
    pg_pass = os.getenv("PG_PASSWORD", "pulse_secure_password")
    pg_port = os.getenv("PG_PORT", "5432")
    pg_db = os.getenv("PG_DATABASE") or os.getenv("PG_DB", "pulse_audit")

    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


def introspect_schema(schema_name: str = None, sample_rows: int = 3, db_url: str = None) -> dict:
    """Returns: { full_table_name: { columns: [...], row_count: int, sample: [...] } }

    Works with both PostgreSQL and SQLite backends via SQLAlchemy reflection.
    """
    db_url = db_url or get_db_url()
    engine_kwargs = {}

    if db_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    try:
        engine = create_engine(db_url, **engine_kwargs)
        inspector = inspect(engine)
        table_names = inspector.get_table_names(schema=schema_name)
    except Exception as e:
        print(f"⚠️ Primary DB connection failed ({db_url}): {e}")
        print("🔄 Falling back to local SQLite database (9gear_pulse.db)...")
        db_url = "sqlite:///./9gear_pulse.db"
        engine = create_engine(
            db_url, connect_args={"check_same_thread": False}
        )
        inspector = inspect(engine)
        table_names = inspector.get_table_names()

    tables: dict = {}

    with engine.connect() as conn:
        for t_name in table_names:
            s_name = schema_name or "public"
            full_key = f"{s_name}.{t_name}" if "sqlite" not in db_url else t_name
            # The reflection calls just below correctly pass schema=
            # to find the right table/columns at all - but these two
            # raw SQL queries never carried that same qualification,
            # so they only ever worked by coincidence when the schema
            # happened to be "public" (on the default search path).
            # SQLite has no schema concept the same way, so only
            # qualify for a real schema-aware engine.
            #
            # Identifier quoting comes from the engine's own dialect,
            # not hand-built double quotes. Hand-quoting broke Snowflake:
            # its SQLAlchemy dialect returns lowercase names for
            # case-insensitive objects (Snowflake stores them uppercase),
            # and a double-quoted lowercase name is a DIFFERENT, nonexistent
            # case-sensitive table there - the count query failed, and the
            # except below silently turned that into row_count = 0. The
            # dialect's own preparer quotes only when a name genuinely
            # needs it, matching how that dialect reflected the name.
            if "sqlite" in db_url:
                qualified_name = f'"{t_name}"'
            else:
                preparer = engine.dialect.identifier_preparer
                qualified_name = f"{preparer.quote_schema(s_name)}.{preparer.quote(t_name)}"

            # Column extraction
            raw_columns = inspector.get_columns(t_name, schema=schema_name)
            columns = [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in raw_columns
            ]

            # Row count & Sample row extraction
            row_count = 0
            sample_data = []

            try:
                # Row Count
                count_res = conn.execute(
                    text(f'SELECT COUNT(*) FROM {qualified_name}')
                )
                row_count = count_res.scalar() or 0

                # Samples
                if sample_rows > 0:
                    sample_res = conn.execute(
                        text(f'SELECT * FROM {qualified_name} LIMIT {sample_rows}')
                    )
                    sample_data = [
                        dict(row._mapping) for row in sample_res.fetchall()
                    ]
            except Exception:
                row_count = 0
                sample_data = []

            tables[full_key] = {
                "schema": s_name,
                "table": t_name,
                "columns": columns,
                "row_count": row_count,
                "sample": sample_data,
            }

    return tables


def _infer_json_type(value) -> str:
    """Maps a Python value (as decoded from JSON) to a type name in the
    same spirit as introspect_schema()'s SQL type strings - not exact
    SQL types (a JSON API has no such thing), but consistent enough for
    the generation prompt to reason about.
    """
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _find_record_list(data, max_depth: int = 2):
    """A REST API's real records are often nested rather than a bare
    top-level array - e.g. {"results": [...]}  or {"data": {"items": [...]}}.
    Looks for the first list of objects, checking the top level first,
    then a bounded depth of nesting - deep enough to catch common
    wrapper patterns without guessing arbitrarily far into an unknown
    response shape. Returns None if nothing list-shaped is found.
    """
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
        if max_depth > 0:
            for value in data.values():
                if isinstance(value, dict):
                    found = _find_record_list(value, max_depth - 1)
                    if found:
                        return found
    return None


def _get_dotted_path(data: dict, path: str):
    """Resolves a dotted field path (e.g. "meta.next_cursor") against a
    nested dict, for cursor-style pagination where the next cursor
    value commonly lives under a nested "meta"/"pagination" object
    rather than at the response's top level. Returns None if any
    segment along the path is missing, rather than raising - a missing
    cursor field is exactly the normal "no more pages" signal, not an
    error.
    """
    current = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _fetch_paginated_records(base_url: str, headers: dict, method: str, request_body: dict, pagination: dict, max_pages: int = 3) -> tuple[list, bool]:
    """Walks up to max_pages pages according to the given pagination
    config, merging every page's records together. Used by
    introspect_api() both to build a more representative sample than a
    single page could (later pages can have different null patterns,
    revealing optional fields page 1 happened not to show) and to
    validate the pagination config actually works at profile-setup
    time - catching a wrong param/field name here, immediately, rather
    than only discovering it later when a real pipeline run silently
    walks zero extra pages.

    Bounded at max_pages regardless of style - this is introspection,
    sampling for shape, never meant to walk an API's entire real
    dataset. Generated pipeline code (see generate_pipeline.py's own
    pagination guidance) walks every real page at actual run time;
    this function never does.

    Pagination parameters go into the query string for GET, or merged
    into the JSON body for POST - matching how a real POST-based
    querying API almost always expects pagination params (the entire
    point of that style is that parameters live in the body, not the
    URL).

    Returns (records, pagination_appeared_to_work) - the second value
    is False if the very first page already returned no next-page
    signal at all (config might be wrong, or the API genuinely only
    has one page of data - both cases are worth surfacing, not
    silently treated as success).
    """
    import requests

    style = pagination.get("style")
    page_size = pagination.get("page_size") or 100
    all_records: list = []
    advanced_past_first_page = False

    def _do_request(params: dict):
        if str(method).upper() == "POST":
            body = {**(request_body or {}), **params}
            return requests.post(base_url, headers=headers, json=body, timeout=15)
        return requests.get(base_url, headers=headers, params=params, timeout=15)

    if style == "offset":
        offset = 0
        for _ in range(max_pages):
            params = {pagination["offset_param"]: offset, pagination["limit_param"]: page_size}
            response = _do_request(params)
            response.raise_for_status()
            page_records = _find_record_list(response.json()) or []
            all_records.extend(page_records)
            if len(page_records) < page_size:
                break
            offset += page_size
            advanced_past_first_page = True

    elif style == "page":
        page_num = 1
        page_size_param = pagination.get("page_size_param")
        for _ in range(max_pages):
            params = {pagination["page_param"]: page_num}
            if page_size_param:
                params[page_size_param] = page_size
            response = _do_request(params)
            response.raise_for_status()
            page_records = _find_record_list(response.json()) or []
            all_records.extend(page_records)
            if len(page_records) < page_size:
                break
            page_num += 1
            advanced_past_first_page = True

    elif style == "cursor":
        cursor = None
        for _ in range(max_pages):
            params = {pagination["cursor_param"]: cursor} if cursor else {}
            response = _do_request(params)
            response.raise_for_status()
            response_json = response.json()
            page_records = _find_record_list(response_json) or []
            all_records.extend(page_records)
            cursor = _get_dotted_path(response_json, pagination["next_cursor_field"])
            if not cursor:
                break
            advanced_past_first_page = True

    return all_records, advanced_past_first_page


def _fetch_oauth2_token(oauth2: dict) -> str:
    """Fetches a real, fresh access token via the OAuth2 client-credentials
    flow (RFC 6749 section 4.4) - the only grant type that fits here,
    since every other grant type (authorization_code, password, etc.)
    genuinely requires a human/browser step this system has nowhere to
    put; this is purely server-to-server.

    Used directly by introspect_api() specifically because introspection
    makes its one sample request immediately - it needs a real, valid
    token right now, unlike generated pipeline code, which fetches (and
    refreshes) its own token at actual execution time instead of
    receiving one pre-fetched here (see api_credentials()'s own
    docstring in connection_service.py for the full reasoning on why
    those two cases are handled differently).

    Sends the request form-encoded (data=...), per RFC 6749's own
    defined format, rather than JSON - the more universally-supported
    convention across real OAuth2 providers, even though some also
    accept JSON.
    """
    import requests

    payload = {
        "grant_type": "client_credentials",
        "client_id": oauth2["client_id"],
        "client_secret": oauth2["client_secret"],
    }
    if oauth2.get("scope"):
        payload["scope"] = oauth2["scope"]
    response = requests.post(oauth2["token_url"], data=payload, timeout=15)
    response.raise_for_status()
    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError(f"OAuth2 token endpoint response had no access_token field: {token_data}")
    return access_token


def introspect_api(
    base_url: str,
    auth_headers: dict = None,
    source_name: str = None,
    sample_rows: int = 3,
    method: str = "GET",
    request_body: dict = None,
    pagination: dict = None,
    oauth2: dict = None,
) -> dict:
    """Introspects a REST API the same way introspect_schema() introspects
    a database - fetches one real sample response and infers field
    names and types from it, returning the SAME
    {name: {columns, row_count, sample}} shape so everything downstream
    (generation, the architecture proposal, the review UI) can treat an
    API source identically to a Postgres one without any special-casing.

    auth_headers is a flat header-name -> value dict (may be empty/None
    for a public API requiring no auth). Covers a single header or many
    (e.g. RapidAPI's X-RapidAPI-Key + X-RapidAPI-Host) identically -
    the caller is responsible for having already assembled the exact
    headers to send, same contract as api_credentials() in
    connection_service.py.

    method/request_body default to a plain GET with no body - the
    original, only behavior this function had before an API source
    could genuinely be POST-based (a request-body search/reporting
    endpoint, GraphQL-adjacent REST). Every existing caller that never
    passes these keeps working completely unchanged.

    pagination is None by default - the original, single-response
    behavior. When provided, walks a bounded number of pages via
    _fetch_paginated_records() rather than fetching just one, both for
    a more representative sample and to validate the config actually
    works at setup time rather than only at real run time.

    oauth2 is None by default - mutually exclusive with auth_headers
    (enforced at validation time in connection_service.py, not here).
    When provided, a real access token is fetched via
    _fetch_oauth2_token() and used as the Authorization header for
    this introspection request - introspection makes its one sample
    request right now, so unlike generated pipeline code (which fetches
    its own token at actual execution time - see api_credentials()'s
    docstring), this genuinely needs a freshly-fetched, currently-valid
    token immediately.

    Never sends sample data anywhere beyond what the API itself already
    returned - same "cache the shape, not the data" principle as
    Postgres introspection, just applied to a JSON response instead of
    information_schema.
    """
    import requests

    headers = dict(auth_headers) if auth_headers else {}
    if oauth2:
        headers["Authorization"] = f"Bearer {_fetch_oauth2_token(oauth2)}"

    pagination_worked = None
    if pagination:
        records, pagination_worked = _fetch_paginated_records(base_url, headers, method, request_body, pagination)
    else:
        if str(method).upper() == "POST":
            response = requests.post(base_url, headers=headers, json=request_body or {}, timeout=15)
        else:
            response = requests.get(base_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        records = _find_record_list(data)
        if records is None:
            records = [data] if isinstance(data, dict) else []

    key = source_name or base_url

    if not records:
        return {key: {"schema": "api", "table": key, "columns": [], "row_count": 0, "sample": []}}

    sampled = records[:sample_rows] if sample_rows > 0 else records[:1]
    all_keys: list = []
    for record in sampled:
        for field_name in record.keys():
            if field_name not in all_keys:
                all_keys.append(field_name)

    columns = [
        {"name": field_name, "type": _infer_json_type(sampled[0].get(field_name)), "nullable": True}
        for field_name in all_keys
    ]

    return {
        key: {
            "schema": "api",
            "table": key,
            "columns": columns,
            "row_count": len(records),
            # sampled[0] is used above purely to infer field names/types
            # (there's no API equivalent of information_schema - seeing
            # one real response is the only way to learn the shape at
            # all) but its actual VALUES are never included here unless
            # sample_rows > 0 was explicitly requested - matching
            # introspect_schema()'s exact contract, which never touches
            # row values at all when shape-only is requested.
            "sample": sampled if sample_rows > 0 else [],
            # Only present when pagination was actually configured -
            # None (the default) would be misleading clutter on every
            # ordinary, non-paginated API profile. False here means the
            # very first page returned no next-page signal at all - the
            # config might be wrong (a typo'd param/field name), or the
            # API genuinely only has one page of data; either way, this
            # surfaces it directly instead of silently treating "walked
            # zero extra pages" as success.
            **({"pagination_worked": pagination_worked} if pagination else {}),
        }
    }


def introspect_graphql(
    endpoint: str,
    query: str,
    variables: dict = None,
    auth_headers: dict = None,
    oauth2: dict = None,
    source_name: str = None,
    sample_rows: int = 3,
) -> dict:
    """Introspects a GraphQL source by running the profile's own
    configured query (with variables) and inferring shape from the
    real result - reusing _find_record_list()/_infer_json_type()
    directly, the same machinery introspect_api() already uses, rather
    than surfacing GraphQL's own __schema introspection capability.

    That's a deliberate choice, not a missed opportunity: __schema
    returns the ENTIRE abstract schema (every type, every field, every
    possible query) for potentially the whole API, which for a large
    real service can be enormous and isn't necessarily more useful to
    generation than seeing the actual real data shape the profile's own
    query will actually return - "sample real data, don't enumerate
    everything possible" is the same principle every other introspection
    function in this module already follows.

    auth is handled identically to introspect_api() - a real OAuth2
    token is fetched right now via _fetch_oauth2_token() when oauth2 is
    configured, for the exact same reason: this makes one real request
    immediately, so it needs a token that's actually valid right now,
    not one generated code will fetch for itself later at real
    execution time.

    GraphQL commonly returns HTTP 200 even when the query itself failed
    - errors live in a separate top-level "errors" array in the
    response body, not reflected in the status code at all, so
    response.raise_for_status() alone would never catch this. Checked
    explicitly here and raised as a clear error, rather than silently
    inferring a shape from an empty or null "data" field - the same
    "don't let a 200-but-actually-broken response through silently"
    lesson already learned the hard way from a real Alpha Vantage
    pipeline earlier in this project's build.
    """
    import requests

    headers = dict(auth_headers) if auth_headers else {}
    if oauth2:
        headers["Authorization"] = f"Bearer {_fetch_oauth2_token(oauth2)}"

    response = requests.post(endpoint, headers=headers, json={"query": query, "variables": variables or {}}, timeout=15)
    response.raise_for_status()
    response_json = response.json()

    errors = response_json.get("errors")
    if errors:
        raise ValueError(f"GraphQL query returned errors (HTTP status was still 200): {errors}")

    data = response_json.get("data")
    if data is None:
        raise ValueError(f"GraphQL response had no 'data' field and no 'errors' either - unexpected response shape: {response_json}")

    key = source_name or endpoint

    records = _find_record_list(data)
    if records is None:
        records = [data] if isinstance(data, dict) else []

    if not records:
        return {key: {"schema": "graphql", "table": key, "columns": [], "row_count": 0, "sample": []}}

    sampled = records[:sample_rows] if sample_rows > 0 else records[:1]
    all_keys: list = []
    for record in sampled:
        for field_name in record.keys():
            if field_name not in all_keys:
                all_keys.append(field_name)

    columns = [
        {"name": field_name, "type": _infer_json_type(sampled[0].get(field_name)), "nullable": True}
        for field_name in all_keys
    ]

    return {
        key: {
            "schema": "graphql",
            "table": key,
            "columns": columns,
            "row_count": len(records),
            "sample": sampled if sample_rows > 0 else [],
        }
    }


def _strip_ns(tag: str) -> str:
    """XML tags come back from ElementTree as '{namespace-uri}LocalName'
    when the source XML declared a namespace - which a real SOAP
    response almost always does. Column names built from the raw,
    namespaced form would be unusable ('{http://schemas.xmlsoap.org/
    soap/envelope/}Body'), so every tag this module deals with is
    stripped down to its local name first.
    """
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_soap_fault(root) -> Optional[str]:
    """Searches the entire parsed response tree for a <Fault> element,
    by local name, regardless of which SOAP version's namespace it
    actually used (1.1: http://schemas.xmlsoap.org/soap/envelope/,
    1.2: http://www.w3.org/2003/05/soap-envelope - stripping namespaces
    first means this one check covers both without needing to know
    which version produced the response).

    Checked unconditionally, independent of the HTTP status code -
    confirmed directly (not assumed) that real-world SOAP fault
    reporting is genuinely inconsistent about accompanying a fault with
    a non-200 status, especially on older/non-compliant services likely
    given SOAP's own prevalence in legacy systems. response.raise_for_status()
    alone is not a reliable enough signal here, the same lesson already
    proven true for both a real Alpha Vantage pipeline and GraphQL's own
    "200 but the query failed" case earlier in this project's build.

    Returns a human-readable message if a fault was found, else None -
    checks for both SOAP 1.1's <faultstring> and 1.2's <Reason> child
    element names, since either could be present depending on which
    version actually produced the response.
    """
    for elem in root.iter():
        if _strip_ns(elem.tag) == "Fault":
            for child in elem.iter():
                local = _strip_ns(child.tag)
                if local in ("faultstring", "Reason", "Text") and child.text and child.text.strip():
                    return child.text.strip()
            return "SOAP fault returned with no faultstring/Reason text."
    return None


def _find_xml_record_list(elem) -> list:
    """Analogous to _find_record_list() for JSON, adapted for XML's
    tree structure instead of nested dicts/lists - XML has no native
    concept of "a list", so a repeated result set instead shows up as
    multiple SIBLING elements sharing the same local tag name (e.g.
    several <Item> elements under an <Items> parent).

    Searches a few levels deep (mirroring _find_record_list's own
    max_depth) for the first parent with 2+ same-named children, and
    returns THOSE children, each converted to a flat {field: text}
    dict. Falls back to treating the entire element as a single record
    (its own children become that one record's fields) if nothing
    repeated is found anywhere - the common case for a SOAP operation
    that returns one object rather than a set.
    """
    def _element_to_record(el) -> dict:
        # Only leaf elements (no children of their own) become fields -
        # a nested element with its own children is a level of the
        # SOAP response's structure, not a real field value most of the
        # time a plain scalar was actually intended.
        return {_strip_ns(child.tag): (child.text or "").strip() for child in el if len(child) == 0}

    def _search(el, depth: int):
        if depth > 4:
            return None
        children = list(el)
        tag_counts: dict = {}
        for child in children:
            tag_counts.setdefault(_strip_ns(child.tag), []).append(child)
        for tag, group in tag_counts.items():
            if len(group) >= 2:
                return group
        for child in children:
            found = _search(child, depth + 1)
            if found is not None:
                return found
        return None

    record_elements = _search(elem, 0)
    if record_elements is not None:
        return [_element_to_record(e) for e in record_elements]

    # No repeated siblings anywhere - treat the whole response as one
    # record. Walks down through any single-child wrapper levels first
    # (a real SOAP response body is almost always wrapped in an
    # operation-response-named element before the actual fields start).
    current = elem
    while len(current) == 1 and len(list(current)[0]) > 0:
        current = list(current)[0]
    record = _element_to_record(current)
    return [record] if record else []


def introspect_soap(
    endpoint: str,
    request_body: str,
    soap_version: str = "1.1",
    soap_action: str = "",
    auth_headers: dict = None,
    oauth2: dict = None,
    source_name: str = None,
    sample_rows: int = 3,
) -> dict:
    """Introspects a SOAP source by sending the profile's own configured
    envelope (request_body) and inferring shape from the real XML
    response - reusing the same "sample real data" principle as every
    other introspection function here, just walking an XML tree instead
    of a JSON structure.

    Headers are built differently depending on soap_version - confirmed
    directly against real, documented SOAP 1.1 vs 1.2 examples before
    writing this: 1.1 uses Content-Type: text/xml plus a separate,
    quoted SOAPAction header; 1.2 embeds the action inside the
    Content-Type header's own action= parameter instead, with no
    separate SOAPAction header at all.

    auth is handled identically to introspect_api()/introspect_graphql() -
    a real OAuth2 token is fetched right now via _fetch_oauth2_token()
    when oauth2 is configured, since this makes one real request
    immediately and needs a token that's actually valid right now.
    """
    import requests
    import xml.etree.ElementTree as ET

    headers = dict(auth_headers) if auth_headers else {}
    if oauth2:
        headers["Authorization"] = f"Bearer {_fetch_oauth2_token(oauth2)}"

    if soap_version == "1.2":
        content_type = 'application/soap+xml; charset=utf-8'
        if soap_action:
            content_type += f'; action="{soap_action}"'
        headers["Content-Type"] = content_type
    else:
        headers["Content-Type"] = "text/xml; charset=utf-8"
        if soap_action:
            headers["SOAPAction"] = f'"{soap_action}"'

    response = requests.post(endpoint, headers=headers, data=request_body.encode("utf-8"), timeout=15)
    # Deliberately NOT response.raise_for_status() before checking for a
    # SOAP fault - a fault is meaningful application data in the
    # response body, worth parsing and reporting with its real
    # faultstring/Reason text, not just surfaced as an opaque "500
    # Server Error" from requests' own generic exception.
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        response.raise_for_status()
        raise ValueError(f"SOAP response was not valid XML: {exc}")

    fault_message = _find_soap_fault(root)
    if fault_message:
        raise ValueError(f"SOAP fault returned: {fault_message}")
    response.raise_for_status()

    records = _find_xml_record_list(root)
    key = source_name or endpoint

    if not records:
        return {key: {"schema": "soap", "table": key, "columns": [], "row_count": 0, "sample": []}}

    sampled = records[:sample_rows] if sample_rows > 0 else records[:1]
    all_keys: list = []
    for record in sampled:
        for field_name in record.keys():
            if field_name not in all_keys:
                all_keys.append(field_name)

    columns = [
        {"name": field_name, "type": _infer_json_type(sampled[0].get(field_name)), "nullable": True}
        for field_name in all_keys
    ]

    return {
        key: {
            "schema": "soap",
            "table": key,
            "columns": columns,
            "row_count": len(records),
            "sample": sampled if sample_rows > 0 else [],
        }
    }


def _infer_csv_type(sample_values: list) -> str:
    """Infers a column's type from its sample string values - CSV has no
    native types, everything csv.DictReader returns is a raw string, so
    this does for CSV what _infer_json_type() does for already-typed
    JSON values, just starting one step earlier.
    """
    non_empty = [v for v in sample_values if v not in (None, "")]
    if not non_empty:
        return "unknown"
    if all(v.strip().lower() in ("true", "false") for v in non_empty):
        return "boolean"
    try:
        for v in non_empty:
            int(v)
        return "integer"
    except ValueError:
        pass
    try:
        for v in non_empty:
            float(v)
        return "float"
    except ValueError:
        pass
    return "string"


def _introspect_csv(path: str, filename: str, sample_rows: int) -> dict:
    """CSV counterpart to introspect_api()'s JSON handling - one pass
    over the file, no full read into memory regardless of file size.
    """
    import csv

    # At least 1 row is always sampled internally for type inference,
    # even when sample_rows=0 - same contract as introspect_api(): the
    # returned "sample" field is what's conditionally withheld, not
    # whether inference itself has data to work from.
    internal_sample_size = sample_rows if sample_rows > 0 else 1

    row_count = 0
    sample_data = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        column_names = reader.fieldnames or []
        for row in reader:
            row_count += 1
            if len(sample_data) < internal_sample_size:
                sample_data.append(dict(row))

    columns = [
        {
            "name": name,
            "type": _infer_csv_type([row.get(name) for row in sample_data]),
            "nullable": True,
        }
        for name in column_names
    ]

    return {
        "schema": "file",
        "table": filename,
        "columns": columns,
        "row_count": row_count,
        "sample": sample_data if sample_rows > 0 else [],
    }


def _introspect_json_file(path: str, filename: str, sample_rows: int) -> dict:
    """JSON counterpart to introspect_api(), reusing the exact same
    _find_record_list()/_infer_json_type() helpers - a JSON API response
    and an uploaded JSON file need identical shape-finding logic, just
    a different way of getting the raw bytes (a file read vs a request).
    """
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records = _find_record_list(data)
    if records is None:
        records = [data] if isinstance(data, dict) else []

    if not records:
        return {"schema": "file", "table": filename, "columns": [], "row_count": 0, "sample": []}

    internal_sample_size = sample_rows if sample_rows > 0 else 1
    sampled = records[:internal_sample_size]

    all_keys: list = []
    for record in sampled:
        for field_name in record.keys():
            if field_name not in all_keys:
                all_keys.append(field_name)

    columns = [
        {"name": field_name, "type": _infer_json_type(sampled[0].get(field_name)), "nullable": True}
        for field_name in all_keys
    ]

    return {
        "schema": "file",
        "table": filename,
        "columns": columns,
        "row_count": len(records),
        "sample": sampled if sample_rows > 0 else [],
    }


def introspect_file(path: str, filename: str, file_format: str, sample_rows: int = 3) -> dict:
    """Introspects a single uploaded file the same way introspect_schema()
    introspects a table - returns the SAME {columns, row_count, sample}
    shape. Dispatches on file_format so a new supported format later
    (TSV, Parquet) means adding one function and one branch here, not
    reworking anything that calls this.
    """
    file_format = (file_format or "").lower()
    if file_format == "csv":
        return _introspect_csv(path, filename, sample_rows)
    if file_format == "json":
        return _introspect_json_file(path, filename, sample_rows)
    raise ValueError(f"Unsupported file format for introspection: {file_format!r}")


def introspect_files(files: list, sample_rows: int = 3) -> dict:
    """Introspects every file belonging to a multi-file upload source,
    merging each file's result into ONE dict keyed by original filename
    - the exact same shape a multi-table Postgres source already
    returns, so a 5-file dataset is structurally identical to a 5-table
    database everywhere downstream (generation, the architecture
    proposal, the review UI never special-case "file source" at all).

    `files` is the list of {storage_path, original_filename, format}
    dicts already resolved from the connection_profile_files table -
    same division of responsibility as introspect_api() taking
    already-resolved auth_headers rather than a ConnectionProfile
    itself: resolving the DB rows is the caller's job, not this
    module's.
    """
    tables: dict = {}
    for file_info in files:
        tables[file_info["original_filename"]] = introspect_file(
            path=file_info["storage_path"],
            filename=file_info["original_filename"],
            file_format=file_info["format"],
            sample_rows=sample_rows,
        )
    return tables


def _introspect_object_keys(keys: list, download_fn, sample_rows: int, schema_label: str) -> dict:
    """The download-then-introspect half shared by every object-store
    source (S3/MinIO, Azure Blob). Each key is downloaded to a temporary
    local file and handed to the EXISTING, unmodified introspect_file(),
    which reads via a plain open(path, ...) and can't take bytes over the
    network - so genuinely reusing its CSV/JSON logic means giving it a
    real local path, not duplicating that logic per storage provider.

    download_fn(key, local_path) is the only provider-specific piece -
    each caller supplies its own SDK's way of fetching one object.

    One unreadable or corrupt object never fails introspection of the
    whole bucket - it's recorded with an "error" field and skipped, the
    same "skip and continue" philosophy introspect_redis() already
    applies to one stale key.
    """
    import tempfile

    tables: dict = {}
    for key in keys:
        file_format = "csv" if key.lower().endswith(".csv") else "json"
        suffix = ".csv" if file_format == "csv" else ".json"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            download_fn(key, tmp_path)
            tables[key] = introspect_file(path=tmp_path, filename=key, file_format=file_format, sample_rows=sample_rows)
        except Exception as exc:
            tables[key] = {"schema": schema_label, "table": key, "columns": [], "row_count": 0, "sample": [], "error": str(exc)}
        finally:
            os.unlink(tmp_path)
    return tables


def introspect_s3(client, bucket: str, prefix: str = "", sample_rows: int = 3, max_objects: int = 20) -> dict:
    """Introspects an S3/MinIO-compatible bucket the same way
    introspect_files() introspects a multi-file upload source - each
    sampled object becomes its own entry in the SAME {name: {columns,
    row_count, sample}} shape everything downstream already expects,
    so an S3 source needs no special-casing anywhere else, matching the
    same design already established for uploaded files and Redis.

    Only .csv and .json objects are considered - introspect_file() only
    supports those two formats, so listing anything else would just be
    discarded work.

    Bounded by max_objects, not the whole bucket - a real bucket can
    hold thousands of objects; this is schema *inference*, not a full
    data read, so a small sample is enough to establish structure. Each
    sampled object is downloaded to a temporary local file and handed
    to the EXISTING, unmodified introspect_file() - which reads via a
    plain open(path, ...) and has no way to accept bytes over the
    network directly - rather than duplicating its CSV/JSON-parsing
    logic for a second, S3-specific code path.

    client is a boto3 S3 client, already configured for the right
    account/region/endpoint (MinIO included) by the caller - resolving
    that is connection_service.py's job, not this module's, the same
    division of responsibility introspect_api() already has for
    already-resolved auth_headers.
    """
    continuation_token = None
    candidates: list = []

    # Paginate list_objects_v2 until either enough CSV/JSON candidates
    # are found or the bucket (or its prefix) is exhausted - a bucket
    # can easily have far more non-matching objects before the first
    # matching one than a single, unbounded MaxKeys call would return.
    while len(candidates) < max_objects:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if prefix:
            kwargs["Prefix"] = prefix
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            lower = key.lower()
            if lower.endswith(".csv") or lower.endswith(".json"):
                candidates.append(key)
                if len(candidates) >= max_objects:
                    break
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    return _introspect_object_keys(
        candidates, lambda key, path: client.download_file(bucket, key, path), sample_rows, "s3",
    )


def introspect_azure_blob(container_client, prefix: str = "", sample_rows: int = 3, max_objects: int = 20) -> dict:
    """Azure Blob counterpart to introspect_s3() - same return shape,
    same CSV/JSON-only filtering, same max_objects bound, and the same
    shared download-then-introspect helper, so an Azure source needs no
    special-casing anywhere downstream either.

    container_client is an azure-storage-blob ContainerClient already
    authenticated by the caller (connection_service.py's job, not this
    module's). list_blobs() returns a lazily-paging iterator, so
    breaking out once max_objects candidates are found stops fetching
    further pages - no manual pagination needed here, unlike S3's
    ContinuationToken loop.
    """
    candidates: list = []
    list_kwargs = {"name_starts_with": prefix} if prefix else {}
    for blob in container_client.list_blobs(**list_kwargs):
        lower = blob.name.lower()
        if lower.endswith(".csv") or lower.endswith(".json"):
            candidates.append(blob.name)
            if len(candidates) >= max_objects:
                break

    def download(key: str, local_path: str) -> None:
        # readinto streams straight to the file handle, rather than
        # readall() holding an entire large blob in memory first.
        with open(local_path, "wb") as f:
            container_client.download_blob(key).readinto(f)

    return _introspect_object_keys(candidates, download, sample_rows, "azure_blob")


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HEX_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
_DIGIT_RE = re.compile(r"^\d+$")
_MAX_STRING_VALUE_CHARS = 500


def _is_variable_key_segment(segment: str) -> bool:
    """A colon-delimited key segment that looks like an ID rather than a
    fixed label - pure digits, a UUID, or a long hex string (a hash or
    token). Real-world Redis keys overwhelmingly follow this
    "label:id:label:id" naming convention (user:1234, session:<uuid>);
    this is what turns a flat list of literal keys into a small number
    of genuinely useful patterns instead of one pattern per key.
    """
    return bool(_DIGIT_RE.match(segment) or _UUID_RE.match(segment) or _HEX_RE.match(segment))


def _key_pattern(key: str) -> str:
    segments = key.split(":")
    return ":".join("*" if _is_variable_key_segment(s) else s for s in segments)


def _truncate(value):
    if isinstance(value, str) and len(value) > _MAX_STRING_VALUE_CHARS:
        return value[:_MAX_STRING_VALUE_CHARS] + f"... [truncated, {len(value)} chars total]"
    return value


def _redis_record_for_key(client, key: str, redis_type: str) -> dict:
    """Maps one Redis key's value to a single flat record, regardless of
    its Redis type - see this module's docstring reasoning above
    introspect_redis() for why "one record per key" is the mapping used
    here rather than, say, one record per list/set member.
    """
    import json as _json

    if redis_type == "string":
        raw = client.get(key)
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return {k: _truncate(v) for k, v in parsed.items()}
        except (TypeError, ValueError):
            pass
        return {"value": _truncate(raw)}
    if redis_type == "hash":
        return {k: _truncate(v) for k, v in client.hgetall(key).items()}
    if redis_type == "list":
        return {"members": [_truncate(v) for v in client.lrange(key, 0, 9)]}
    if redis_type == "set":
        return {"members": [_truncate(v) for v in client.srandmember(key, 10)]}
    if redis_type == "zset":
        return {"members": [_truncate(v) for v, _score in client.zrange(key, 0, 9, withscores=True)]}
    if redis_type == "stream":
        entries = client.xrange(key, count=5)
        return {"entries": [{"id": entry_id, **{k: _truncate(v) for k, v in fields.items()}} for entry_id, fields in entries]}
    return {}


def introspect_redis(redis_url: str, sample_size: int = 200, max_scan_batches: int = 50) -> dict:
    """Introspects a Redis keyspace the same way introspect_schema()
    introspects a database - returns the SAME {name: {schema, table,
    columns, row_count, sample}} shape everything downstream already
    expects, so a Redis source needs no special-casing anywhere else.

    Deliberately never uses KEYS * - that command is O(N) and blocks
    the entire Redis server for its whole duration, a real production
    hazard against a live instance. SCAN is cursor-based and
    non-blocking, examining a bounded batch per call; this stops once
    either sample_size keys have been seen or max_scan_batches SCAN
    calls have been made, whichever comes first, so this never attempts
    to walk an entire large production keyspace.

    Real-world Redis keys overwhelmingly follow a colon-delimited
    "label:id:label:id" convention (user:1234, session:<uuid>:data).
    Sampled keys are grouped by PATTERN (the ID-like segments replaced
    with *) rather than returned as a flat list of literal keys - a
    keyspace with thousands of individual user:<id> keys collapses to
    one genuinely useful "user:*" entry instead of thousands of
    near-duplicate ones. Each pattern becomes one "table" entry; each
    sampled key within that pattern becomes one "row" via
    _redis_record_for_key(), so a hash's fields become that row's
    columns, a string becomes a single "value" column (or is treated as
    a flat object if the string itself is a JSON-encoded dict), and
    list/set/zset become a single "members" column - the one mapping
    that stays sensible for every Redis type without forcing
    generation to special-case each one differently.

    row_count here is the number of keys in the SAMPLE matching this
    pattern, not a true count across the whole keyspace (SCAN is
    non-blocking specifically because it never does that count) - the
    same honest limitation introspect_api() already has for its own
    row_count (records in the one response fetched, not a true total
    across pages).
    """
    import redis as redis_lib

    client = redis_lib.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=10, socket_timeout=10)
    try:
        cursor = 0
        sampled_keys: list = []
        batches = 0
        while batches < max_scan_batches and len(sampled_keys) < sample_size:
            cursor, keys = client.scan(cursor=cursor, count=100)
            sampled_keys.extend(keys)
            batches += 1
            if cursor == 0:
                break
        sampled_keys = sampled_keys[:sample_size]

        groups: dict = {}
        for key in sampled_keys:
            groups.setdefault(_key_pattern(key), []).append(key)

        tables: dict = {}
        for pattern, keys_in_group in groups.items():
            example_key = keys_in_group[0]
            redis_type = client.type(example_key)
            if redis_type == "none":
                # Expired/evicted between SCAN and TYPE - skip rather
                # than fail the whole introspection over one stale key.
                continue

            records = [_redis_record_for_key(client, k, redis_type) for k in keys_in_group[:10]]
            all_field_names: list = []
            for record in records:
                for field_name in record.keys():
                    if field_name not in all_field_names:
                        all_field_names.append(field_name)

            columns = [
                {"name": field_name, "type": _infer_json_type(records[0].get(field_name)) if records else "unknown", "nullable": True}
                for field_name in all_field_names
            ]

            tables[pattern] = {
                "schema": "redis",
                "table": pattern,
                "columns": columns,
                "row_count": len(keys_in_group),
                "sample": records,
                # Genuinely Redis-specific context generation needs that
                # has no equivalent in the standard columns/row_count/
                # sample shape - the real Redis TYPE (so generated code
                # calls HGETALL vs GET vs LRANGE correctly) and one
                # concrete example key (so a SCAN MATCH expression can
                # be written correctly against the real naming
                # convention, not guessed at from the pattern alone).
                "redis_type": redis_type,
                "example_key": example_key,
            }
        return tables
    finally:
        client.close()


if __name__ == "__main__":
    import json

    schema = introspect_schema()
    print(json.dumps(schema, indent=2, default=json_serial))
