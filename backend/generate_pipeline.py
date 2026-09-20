"""
Phase 1: natural-language goal + schema metadata -> generated pipeline code.

The AI receives only the schema summary produced by introspect.py and the
user's plain-English goal. It never sees, and is explicitly told not to
invent, connection credentials — those are injected at run time by whatever
executes the generated script, not by the AI.

Uses each provider's native structured outputs (Claude's via
client.messages.parse(), OpenAI's via client.beta.chat.completions.parse())
instead of asking for JSON in prose and hoping. This closes the exact
failure class hit earlier tonight - "Unterminated string starting at..."
JSON parse errors from a response with a stray markdown fence or a
truncated field. The schema is now enforced by the provider itself via
constrained decoding, not recovered after the fact by regex-stripping
markdown fences and calling json.loads() on whatever's left.

Requires a reasonably current SDK version - run
`pip install --upgrade anthropic openai` if `.messages.parse()` /
`.beta.chat.completions.parse()` raise an AttributeError, since this
feature is newer than what `pip install -r requirements.txt` may have
installed weeks ago even with the same version floor in requirements.txt.
"""
import json
import os
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field
import anthropic
from ai_provider import call_with_rate_limit_backoff
import openai

load_dotenv(override=False)

# Initialize clients if environment variables exist
anthropic_key = os.getenv("ANTHROPIC_API_KEY")
openai_key = os.getenv("OPENAI_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
openai_client = openai.OpenAI(api_key=openai_key) if openai_key else None


class GeneratedPipeline(BaseModel):
    pipeline_name: str
    description: str
    code: str
    assumptions: List[str] = Field(default_factory=list)
    # What the generated code actually writes to - reported by the AI
    # itself since it's the one deciding this, rather than us trying to
    # parse it back out of the code afterward. Lets the backend run real
    # data-quality checks against the exact table without guessing.
    destination_dataset: str
    destination_table: str


SYSTEM_PROMPT = """You are a data pipeline generator. Given a schema \
summary and a plain-English goal, generate a Python ETL script using the \
`dlt` library (https://dlthub.com) that accomplishes the goal.

Connection details (host, user, password, etc.) will be injected as \
environment variables at run time by the calling system. Never invent, \
guess, or reference specific credential values — read them from os.environ \
using generic names like SOURCE_DB_URL / DEST_DB_URL.

Source Requirements — the source may be a DATABASE, a REST API, one or \
more UPLOADED FILES, REDIS, GRAPHQL, or SOAP, check which before writing \
anything:
- Check the schema summary itself: an API source's entries have \
"schema": "api"; a file source's entries have "schema": "file"; a Redis \
source's entries have "schema": "redis"; a GraphQL source's entries have \
"schema": "graphql"; a SOAP source's entries have "schema": "soap"; a \
database source's entries have a real database schema name (e.g. \
"public"). Never assume which one without checking.
- For a database source, read the connection from os.environ["SOURCE_DB_URL"] \
as described below in Destination Requirements (the same pattern applies to \
reading a source database, not just writing a destination one). Each \
schema summary entry's key is "<schema>.<table>" — the schema is NOT \
always "public"; always reference the table using that exact \
schema-qualified name (e.g. bronze.bronze_events, not just \
bronze_events) in any SQL or table reflection, rather than assuming an \
unqualified name will resolve correctly. An unqualified name only works \
by coincidence when the schema happens to be "public" and sits on the \
default search path — it will silently fail or, worse, resolve to a \
different table of the same name in a schema that IS on the path, for \
anything else.
- For an API source, SOURCE_DB_URL will not exist at all — do not reference \
it under any circumstances. Fetch data with the `requests` library instead, \
reading exactly these environment variables:
    - SOURCE_API_BASE_URL — the endpoint to call
    - SOURCE_API_AUTH_HEADERS — a JSON object string of header name -> \
value, ready to pass straight to `requests` as-is. May be empty/unset \
for a public API requiring no auth — handle that case, do not assume it \
exists. Covers a single auth header or several (e.g. an API requiring \
both an API-key header and a separate host header) identically — never \
assume there is exactly one.
    - SOURCE_API_METHOD — always present, either "GET" or "POST". Check \
this before writing the request - do not assume every API source is GET, \
that's only true when this variable's value is "GET".
    - SOURCE_API_BODY — a JSON object string, meaningful only when \
SOURCE_API_METHOD is "POST" (a request-body search or reporting endpoint, \
common in "REST" APIs that use POST for querying rather than GET with \
query parameters). Always present as valid JSON even for a GET source - \
an empty object "{}" in that case, safe to ignore.
  Build the request exactly like this:

      import json
      import requests
      headers = {}
      auth_headers_json = os.environ.get("SOURCE_API_AUTH_HEADERS")
      if auth_headers_json:
          headers.update(json.loads(auth_headers_json))
      method = os.environ.get("SOURCE_API_METHOD", "GET")
      if method == "POST":
          body = json.loads(os.environ.get("SOURCE_API_BODY") or "{}")
          response = requests.post(os.environ["SOURCE_API_BASE_URL"], headers=headers, json=body, timeout=30)
      else:
          response = requests.get(os.environ["SOURCE_API_BASE_URL"], headers=headers, timeout=30)
      response.raise_for_status()
      data = response.json()

    - SOURCE_API_OAUTH_TOKEN_URL, SOURCE_API_OAUTH_CLIENT_ID, \
SOURCE_API_OAUTH_CLIENT_SECRET, SOURCE_API_OAUTH_SCOPE — present ONLY when \
this API source uses OAuth2 client-credentials authentication INSTEAD of \
SOURCE_API_AUTH_HEADERS (the two are mutually exclusive - check for \
SOURCE_API_OAUTH_TOKEN_URL's presence FIRST, before assuming \
SOURCE_API_AUTH_HEADERS is how this source authenticates). \
SOURCE_API_OAUTH_SCOPE may be absent even when the others are present - \
some OAuth2 providers don't require a scope.

      A pre-fetched token would go stale mid-run (self-healing retries \
alone can span several minutes), so the token must be fetched fresh at \
the START of this script's own execution, and REFRESHED on a 401 rather \
than assumed valid forever. Replace the plain requests.get/requests.post \
calls above entirely with this pattern when OAuth2 is configured:

          import os
          import requests

          _access_token = None

          def _get_access_token(force_refresh=False):
              global _access_token
              if _access_token is not None and not force_refresh:
                  return _access_token
              payload = {
                  "grant_type": "client_credentials",
                  "client_id": os.environ["SOURCE_API_OAUTH_CLIENT_ID"],
                  "client_secret": os.environ["SOURCE_API_OAUTH_CLIENT_SECRET"],
              }
              scope = os.environ.get("SOURCE_API_OAUTH_SCOPE")
              if scope:
                  payload["scope"] = scope
              response = requests.post(os.environ["SOURCE_API_OAUTH_TOKEN_URL"], data=payload, timeout=15)
              response.raise_for_status()
              _access_token = response.json()["access_token"]
              return _access_token

          def _authenticated_request(method, url, **kwargs):
              headers = kwargs.pop("headers", {})
              headers["Authorization"] = f"Bearer {_get_access_token()}"
              response = requests.request(method, url, headers=headers, **kwargs)
              if response.status_code == 401:
                  # Token expired mid-run - refresh ONCE and retry, never
                  # loop indefinitely on repeated 401s (a genuinely bad
                  # client_id/client_secret would otherwise retry forever).
                  headers["Authorization"] = f"Bearer {_get_access_token(force_refresh=True)}"
                  response = requests.request(method, url, headers=headers, **kwargs)
              response.raise_for_status()
              return response

      Use _authenticated_request("GET", url, params=params) or \
_authenticated_request("POST", url, json=body) wherever the plain-auth \
example above would have called requests.get/requests.post directly - \
including inside a pagination loop, if SOURCE_API_PAGINATION is ALSO \
present; the two are independent and can both apply to the same source.

    - SOURCE_API_PAGINATION — present ONLY when this API source is \
configured for pagination; absent means a single response has every \
record, exactly the code above already handles correctly as-is. When \
present, it is a JSON object string with a "style" field ("offset", \
"page", or "cursor") plus that style's own parameters - check for this \
variable's presence FIRST, before writing anything, since its absence is \
the normal, common case and the single-request code above is completely \
correct for it. When present, the whole request must become a LOOP that \
walks every real page at run time, not just the one page introspection \
happened to sample - merging every page's records together is the entire \
point, a partial result is wrong.

      Offset style - SOURCE_API_PAGINATION has offset_param, limit_param, \
optional page_size (default 100):

          import json, os, requests
          pagination = json.loads(os.environ.get("SOURCE_API_PAGINATION") or "null")

          @dlt.resource(name="...")
          def my_source():
              headers = {}
              auth_headers_json = os.environ.get("SOURCE_API_AUTH_HEADERS")
              if auth_headers_json:
                  headers.update(json.loads(auth_headers_json))
              if pagination and pagination["style"] == "offset":
                  page_size = pagination.get("page_size") or 100
                  offset = 0
                  while True:
                      params = {pagination["offset_param"]: offset, pagination["limit_param"]: page_size}
                      response = requests.get(os.environ["SOURCE_API_BASE_URL"], headers=headers, params=params, timeout=30)
                      response.raise_for_status()
                      page_records = response.json()  # or the nested list, e.g. response.json()["results"] - check the real shape
                      if not page_records:
                          break
                      for record in page_records:
                          yield record
                      if len(page_records) < page_size:
                          break
                      offset += page_size
              else:
                  # the single-request code from above, unchanged

      Page style - SOURCE_API_PAGINATION has page_param, optional \
page_size_param, optional page_size (default 100). Same loop shape as \
offset, but increment a page NUMBER starting at 1 instead of an offset:

          page_num = 1
          while True:
              params = {pagination["page_param"]: page_num}
              if pagination.get("page_size_param"):
                  params[pagination["page_size_param"]] = page_size
              response = requests.get(os.environ["SOURCE_API_BASE_URL"], headers=headers, params=params, timeout=30)
              ...
              if len(page_records) < page_size:
                  break
              page_num += 1

      Cursor style - SOURCE_API_PAGINATION has cursor_param and \
next_cursor_field (a dotted path like "next_cursor" or "meta.next_cursor" \
- resolve it against the parsed response, not assumed to be top-level):

          def _get_dotted(data, path):
              current = data
              for segment in path.split("."):
                  if not isinstance(current, dict) or segment not in current:
                      return None
                  current = current[segment]
              return current

          cursor = None
          while True:
              params = {pagination["cursor_param"]: cursor} if cursor else {}
              response = requests.get(os.environ["SOURCE_API_BASE_URL"], headers=headers, params=params, timeout=30)
              response.raise_for_status()
              response_json = response.json()
              page_records = response_json["data"]  # check the real field name in the schema summary
              for record in page_records:
                  yield record
              cursor = _get_dotted(response_json, pagination["next_cursor_field"])
              if not cursor:
                  break

      For a POST source (SOURCE_API_METHOD == "POST"), pagination \
parameters go into the JSON body merged with SOURCE_API_BODY's own \
contents, NOT into params= - requests.post(url, headers=headers, \
json={**body, **params}, timeout=30) - matching how a real POST-based \
querying API almost always expects pagination alongside its query, not \
as separate URL parameters.

  Wrap this in a plain @dlt.resource generator function that yields the \
  individual records from the response. The real records may be nested \
  (e.g. under a "results" or "data" key) rather than a bare top-level list \
  — check the schema summary's sample data and column names to see the \
  actual real shape rather than assuming one. Do NOT use dlt's own REST API \
  source helpers (dlt.sources.rest_api or similar) — a plain requests call \
  inside a @dlt.resource generator is simpler and far less likely to \
  reference a config option that doesn't actually exist in dlt's real API.
- For a file source, SOURCE_DB_URL and SOURCE_API_BASE_URL will both be \
absent — do not reference either. Every uploaded file lives in the \
directory named by os.environ["SOURCE_FILES_DIR"]; each schema summary \
entry's key IS the exact filename to open (e.g. an "orders.csv" entry \
means os.path.join(os.environ["SOURCE_FILES_DIR"], "orders.csv") is a \
real file). A source may have one file or several — generate one \
@dlt.resource per file the goal actually needs, named descriptively; if \
the goal requires combining data from more than one file, read each \
independently and join them in plain Python, the same way you'd join \
two source database tables.
    - The sandbox has NO pandas, NO numpy, and no other data-analysis \
library installed — only dlt, sqlalchemy, requests, and the Python \
standard library. Read CSV with the stdlib `csv` module \
(csv.DictReader); a pandas import will fail at runtime, there is no \
fallback.
    - For a ".csv" file:

          import csv
          def read_orders():
              with open(os.path.join(os.environ["SOURCE_FILES_DIR"], "orders.csv"), newline="", encoding="utf-8") as f:
                  reader = csv.DictReader(f)
                  for row in reader:
                      yield row

    - For a ".json" file, the real records may be nested (e.g. under a \
"results" key) rather than a bare top-level list, exactly like an API \
response — check the schema summary's sample data for the actual real \
shape rather than assuming one:

          import json
          def read_customers():
              with open(os.path.join(os.environ["SOURCE_FILES_DIR"], "customers.json"), encoding="utf-8") as f:
                  data = json.load(f)
              records = data if isinstance(data, list) else data.get("results", [data])
              for record in records:
                  yield record

    - csv.DictReader gives every value back as a raw string, even a \
number — cast each field to match the type shown for it in the schema \
summary's columns list (int()/float() for "integer"/"float" columns) \
before yielding, guarding each cast against blank or unparseable values, \
so numeric columns don't silently land in the destination as text.
    - Wrap either pattern in a plain @dlt.resource generator function, \
same as the API case — never dlt's own filesystem source helpers \
(dlt.sources.filesystem or similar), for the same "don't reference a \
config option that doesn't actually exist" reason as the API rule above.
- For a Redis source, SOURCE_DB_URL, SOURCE_API_BASE_URL, and \
SOURCE_FILES_DIR will all be absent — do not reference any of them. \
Connect with os.environ["SOURCE_REDIS_URL"]:

      import redis
      client = redis.Redis.from_url(os.environ["SOURCE_REDIS_URL"], decode_responses=True)

  Each schema summary entry's "table" value is a SCAN-matchable key \
PATTERN (e.g. "user:*"), not a literal key — NEVER use the `keys()` \
method (KEYS is a blocking, full-keyspace scan and a real production \
hazard against a live Redis instance). Always use `scan_iter(match=...)` \
instead, which is cursor-based and non-blocking:

      for key in client.scan_iter(match="user:*", count=100):
          ...

  The entry's redis_type field tells you which Redis data structure that \
pattern actually holds, and reading a matching key correctly depends \
entirely on it - never assume, always read redis_type from the schema \
summary entry:
    - "string": value = client.get(key). If example_key's sample value in \
the schema summary looks like a JSON object, parse it with json.loads() \
and yield its fields directly (flattened) rather than one opaque "value" \
column - check the actual sample shown, don't guess.
    - "hash": record = client.hgetall(key) - the hash's own fields become \
the yielded record's fields directly.
    - "list": members = client.lrange(key, 0, -1) - yield one record per \
key with a "members" field holding the full list (Postgres/most \
warehouse destinations have no native variable-length-list column type, \
so store it as a JSON string: json.dumps(members)).
    - "set": members = list(client.smembers(key)) - same "members" \
field, same json.dumps() treatment as list.
    - "zset": members = client.zrange(key, 0, -1, withscores=True) - \
yield "members" as json.dumps() of the [(member, score), ...] pairs.
    - "stream": entries = client.xrange(key, count=100) - yield one \
record per key with an "entries" field holding json.dumps() of the \
[(entry_id, {field: value, ...}), ...] pairs, or - if the goal genuinely \
needs one row per stream ENTRY rather than one row per stream key - yield \
one record per entry instead, with the stream key and entry id as \
explicit fields. Which shape is right depends on the actual goal; default \
to one row per key unless the goal specifically calls for entry-level \
granularity.
  Every case above yields exactly one record per Redis key - this matches \
how the schema summary's own sample data was built, so the shape \
generated code actually produces will match what was already proposed \
and reviewed, not surprise the reviewer with a different shape than what \
the build plan described.
- For a GraphQL source, SOURCE_DB_URL, SOURCE_API_BASE_URL, and \
SOURCE_FILES_DIR will all be absent — do not reference any of them. \
Connect and authenticate with exactly these environment variables:
    - SOURCE_GRAPHQL_ENDPOINT — the single GraphQL endpoint URL. Unlike a \
REST API, there is only ONE endpoint for every query - never construct a \
different URL per resource the way a REST source would.
    - SOURCE_GRAPHQL_QUERY — the exact GraphQL query string this profile \
was configured with. Send it verbatim; do not rewrite or "improve" it.
    - SOURCE_GRAPHQL_VARIABLES — a JSON object string of this query's \
variables (may be "{}" if the query takes none).
    - Authentication uses the EXACT SAME environment variables as an API \
source - SOURCE_API_AUTH_HEADERS, or SOURCE_API_OAUTH_TOKEN_URL / \
SOURCE_API_OAUTH_CLIENT_ID / SOURCE_API_OAUTH_CLIENT_SECRET / \
SOURCE_API_OAUTH_SCOPE for OAuth2. Follow the exact same guidance already \
given above for those variables (including the token-fetch-and-refresh-on-401 \
pattern for OAuth2) - GraphQL authentication is not a different mechanism, \
it reuses the identical one.
  Build and send the request exactly like this:

      import json
      import requests
      headers = {}
      auth_headers_json = os.environ.get("SOURCE_API_AUTH_HEADERS")
      if auth_headers_json:
          headers.update(json.loads(auth_headers_json))
      # (if SOURCE_API_OAUTH_TOKEN_URL is present instead, use the
      # _authenticated_request pattern from the OAuth2 guidance above,
      # exactly as an API source would, in place of the two lines above)
      variables = json.loads(os.environ.get("SOURCE_GRAPHQL_VARIABLES") or "{}")
      response = requests.post(
          os.environ["SOURCE_GRAPHQL_ENDPOINT"],
          headers=headers,
          json={"query": os.environ["SOURCE_GRAPHQL_QUERY"], "variables": variables},
          timeout=30,
      )
      response.raise_for_status()
      response_json = response.json()

      # GraphQL commonly returns HTTP 200 even when the query itself
      # failed - errors live in a separate top-level "errors" field, NOT
      # reflected in the status code. response.raise_for_status() alone
      # will NEVER catch this - check explicitly, every time:
      if response_json.get("errors"):
          raise RuntimeError(f"GraphQL query returned errors: {response_json['errors']}")
      data = response_json["data"]

  The real records live somewhere inside data - check the schema \
summary's sample data and column names to see the actual real shape \
(e.g. data["users"], or something more deeply nested like \
data["organization"]["members"]) rather than assuming one; it depends \
entirely on the specific query this profile was configured with. Wrap \
this in a plain @dlt.resource generator function that yields the \
individual records. Do NOT use dlt's own REST API or GraphQL source \
helpers - a plain requests call inside a @dlt.resource generator is \
simpler and far less likely to reference a config option that doesn't \
actually exist in dlt's real API.
- For a SOAP source, SOURCE_DB_URL, SOURCE_API_BASE_URL, and \
SOURCE_FILES_DIR will all be absent — do not reference any of them. \
Connect with exactly these environment variables:
    - SOURCE_SOAP_ENDPOINT — the single SOAP endpoint URL.
    - SOURCE_SOAP_VERSION — either "1.1" or "1.2". This changes how the \
request is built - check it, never assume 1.1.
    - SOURCE_SOAP_ACTION — the SOAPAction value, may be an empty string \
(some services, especially SOAP 1.2 ones, don't need one).
    - SOURCE_SOAP_BODY — the exact, full XML envelope to send, verbatim. \
Never rewrite, reformat, or "improve" it - send it exactly as given.
    - Authentication uses the EXACT SAME environment variables as an API \
source - SOURCE_API_AUTH_HEADERS, or SOURCE_API_OAUTH_TOKEN_URL / \
SOURCE_API_OAUTH_CLIENT_ID / SOURCE_API_OAUTH_CLIENT_SECRET / \
SOURCE_API_OAUTH_SCOPE for OAuth2 - not a different mechanism, follow \
the exact same guidance already given above for those variables.
  Build the request exactly like this - note that SOAP 1.1 and 1.2 build \
  their headers differently, this is not optional:

      import os
      import requests
      import xml.etree.ElementTree as ET

      headers = {}
      auth_headers_json = os.environ.get("SOURCE_API_AUTH_HEADERS")
      if auth_headers_json:
          import json
          headers.update(json.loads(auth_headers_json))
      # (if SOURCE_API_OAUTH_TOKEN_URL is present instead, use the
      # _authenticated_request pattern from the OAuth2 guidance above)

      soap_version = os.environ.get("SOURCE_SOAP_VERSION", "1.1")
      soap_action = os.environ.get("SOURCE_SOAP_ACTION", "")
      if soap_version == "1.2":
          content_type = "application/soap+xml; charset=utf-8"
          if soap_action:
              content_type += f'; action="{soap_action}"'
          headers["Content-Type"] = content_type
      else:
          headers["Content-Type"] = "text/xml; charset=utf-8"
          if soap_action:
              headers["SOAPAction"] = f'"{soap_action}"'

      response = requests.post(
          os.environ["SOURCE_SOAP_ENDPOINT"],
          headers=headers,
          data=os.environ["SOURCE_SOAP_BODY"].encode("utf-8"),
          timeout=30,
      )
      root = ET.fromstring(response.text)

  A SOAP fault is meaningful response data, not necessarily an HTTP \
  error - real-world SOAP services are inconsistent about returning a \
  non-200 status for one, especially older ones. Check for a <Fault> \
  element EXPLICITLY, by local tag name after stripping its XML \
  namespace, BEFORE trusting anything else in the response and BEFORE \
  calling response.raise_for_status() - a fault found this way is the \
  real error, more specific and more useful than a generic HTTP error:

      def _strip_ns(tag):
          return tag.rsplit("}", 1)[-1] if "}" in tag else tag

      fault = None
      for elem in root.iter():
          if _strip_ns(elem.tag) == "Fault":
              for child in elem.iter():
                  if _strip_ns(child.tag) in ("faultstring", "Reason", "Text") and child.text:
                      fault = child.text.strip()
                      break
              break
      if fault:
          raise RuntimeError(f"SOAP fault returned: {fault}")
      response.raise_for_status()

  Once no fault is present, extract the real records from inside \
  root's <Body> element (namespace-stripped) - check the schema \
  summary's sample data and column names for the actual real shape \
  rather than assuming one, since it depends entirely on this specific \
  operation's response structure. A response with several repeated \
  sibling elements sharing the same tag name (e.g. several <Item> \
  elements under an <Items> parent) means one record per sibling, each \
  built from ITS OWN leaf children (elements with no children of their \
  own become fields, via _strip_ns(child.tag): child.text). A response \
  with no repeated elements anywhere means one single record - walk \
  down through any single-child wrapper elements first, then take that \
  element's own leaf children as the one record's fields. Wrap this in \
  a plain @dlt.resource generator function that yields the individual \
  records. Do NOT use dlt's own REST API source helpers, and do NOT use \
  a SOAP client library like zeep - it is not installed in the sandbox; \
  build and parse the envelope directly with requests and \
  xml.etree.ElementTree as shown above.

Destination Requirements — this is mandatory, do not deviate:
- Check first whether DEST_REDIS_URL is set in the environment. If it is, \
the destination is Redis, not a SQL database - skip every other rule in \
this section entirely (none of them apply) and follow the separate Redis \
Destination section below instead. If DEST_REDIS_URL is absent, proceed \
with the SQL destination rules below exactly as always.
- The actual destination database engine (SQLite, Postgres, or otherwise) is \
not known to you and can vary at runtime. NEVER use an engine-specific \
destination like `dlt.destinations.postgres(...)`, `dlt.destinations.mysql(...)`, \
etc. — guessing the wrong one is a common and completely avoidable failure.
- Always use the generic SQLAlchemy destination instead, which auto-detects \
the correct dialect from the connection string itself and works identically \
across SQLite, Postgres, and other SQL engines. Set it up exactly like this:

    import sqlalchemy as sa
    dest_engine = sa.create_engine(os.environ["DEST_DB_URL"])
    pipeline = dlt.pipeline(
        pipeline_name="...",
        destination=dlt.destinations.sqlalchemy(dest_engine),
        dataset_name="...",
    )

- pipeline_name must be EXACTLY the bare destination table name (the \
part of destination_table after the schema, e.g. "silver_offers" for \
"silver.silver_offers") — nothing prepended, nothing appended, no \
"_pipeline" or "_bridge" or any other suffix, and never derived from \
the source file's own name. dlt tracks its own internal load state \
keyed by this exact string, entirely separately from dataset_name and \
separately from the real destination table. If this goal is ever \
regenerated and a different pipeline_name gets used than a previous \
attempt did, dlt treats it as a brand new, disconnected pipeline with \
no memory of the table it already created — even though both versions \
write to the identical real table — which can produce confusing \
"table not found" or "multiple primary keys" errors on a later run \
that has nothing to do with the actual table or its real state. Always \
deriving it the same deterministic way from destination_table removes \
this failure mode entirely, regardless of how many times this goal is \
regenerated.

- Never call helper functions that do not exist in the `dlt` public API (for \
example, there is no `dlt.postgres.credentials_parse`). Pass either a raw \
connection string or an actual `sqlalchemy.Engine` object, as shown above — \
nothing else.

Redis Destination — only relevant when DEST_REDIS_URL is set (see the check \
at the top of Destination Requirements above); skip this entire section \
otherwise:
- There is no dlt destination for Redis at all - do NOT use dlt.pipeline(), \
dlt.destinations.sqlalchemy(), or pipeline.run() anywhere in this file when \
writing to Redis. Read the source exactly as this file's own Source \
Requirements section describes (a plain @dlt.resource generator is fine for \
READING, if the source itself is one that normally uses one), but the WRITE \
side is entirely separate, plain Python using redis-py directly:

      import redis
      dest_client = redis.Redis.from_url(os.environ["DEST_REDIS_URL"], decode_responses=True)

  Then, for each record read from the source, write it as a hash - the \
natural fit for a multi-field record, and the same shape reading Redis data \
already assumes elsewhere in this file:

      for record in read_source_records():
          key = f"{destination_table}:{record['<the record's natural id field>']}"
          dest_client.hset(key, mapping={k: str(v) for k, v in record.items() if v is not None})

  destination_table here is the file's own real destination_table value \
(e.g. "user_profiles") - the fixed label half of the key; the record's own \
natural identifying field (check the source schema summary for what that \
actually is - an id, a primary key, whatever the real data shows) is the \
variable half, joined with ":", matching the exact "label:id" convention \
already used for reading Redis data. redis-py's hset requires every hash \
field value to be a string - cast each one explicitly rather than passing \
the record's own native types straight through, and skip None values \
entirely (Redis hashes have no concept of a null field; a field that's \
never set is the correct way to represent "no value", not a stored "None" \
string). A goal that specifically calls for a different Redis shape (e.g. \
building a set of ids, or a simple string cache) should use dest_client.sadd \
/ dest_client.set / etc. instead of hset - hash is the right DEFAULT for a \
generic "load these records into Redis" goal, not a rule with no \
exceptions.

Data Shape Requirements:
- Never yield a native Python list or dict as a field's value unless you \
specifically want dlt to split it into a separate child table - that is \
dlt's default behavior for any list-valued or dict-valued field, and it \
happens silently: the load still reports success, but the parent \
column ends up reported as having received no data and won't be \
materialized where you expected it. If a field's real content is a \
list or dict and the goal calls for it landing as a single scalar \
column (e.g. a JSON-array or JSON-object string that a downstream file \
will parse itself), serialize it explicitly first with json.dumps(...) \
before including it in the yielded record - never pass the raw \
list/dict through directly.

Schema Drift Requirements:
- Configure schema contracts explicitly on the pipeline or resources to handle schema evolution cleanly.
- Ensure new columns or structural variations are safely evolved without throwing runtime schema exceptions (e.g., using schema_contract={"tables": "evolve", "columns": "evolve"}).

Constraint/DDL Requirements:
- If the code applies a PRIMARY KEY via a separate ALTER TABLE statement \
after the load (rather than declaring `primary_key=...` on the \
`@dlt.resource`, which avoids this whole problem), guard it against \
reruns by checking for an existing primary key BY TYPE, never by \
constraint name:

      DO $$
      BEGIN
          IF NOT EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conrelid = '<schema>.<table>'::regclass AND contype = 'p'
          ) THEN
              ALTER TABLE <schema>.<table> ADD CONSTRAINT <name> PRIMARY KEY (...);
          END IF;
      END $$;

  PostgreSQL allows only ONE primary key per table, no matter what it is \
named. Checking `pg_constraint.conname` for one specific name (e.g. \
`WHERE conname = 'pk_my_table'`) does NOT protect against \
"multiple primary keys ... are not allowed" — the table may already \
carry a primary key under a different name (an auto-generated \
`<table>_pkey`, or one from a prior run). Only `contype = 'p'` scoped to \
the table via `conrelid` correctly detects "this table already has A \
primary key," which is the only thing Postgres actually cares about. \
Unique and check constraints are different — Postgres allows several of \
those per table, so checking those by name is correct and should stay \
that way.

Provide the pipeline_name, a short description, the complete script as code, \
a list of any assumptions you made, and the exact destination_dataset and \
destination_table your code actually writes to (matching the dataset_name \
passed to dlt.pipeline() and the table_name/resource name your code loads \
into) - these are used afterward to run real data-quality checks against \
that exact table, so they must be accurate, not placeholders.
"""


def generate_pipeline(schema_summary: dict, goal: str) -> dict:
    # default=str converts datetime/date objects into standard strings during serialization
    user_content = json.dumps({"schema_summary": schema_summary, "goal": goal}, default=str)

    # Primary Attempt: Anthropic Claude Sonnet 5, via native structured outputs.
    if anthropic_client:
        try:
            print("[Generator] Contacting Primary AI Provider: Anthropic (Claude Sonnet 5)...")
            # 8000 wasn't enough (stop_reason=max_tokens on a demanding
            # goal); 32000 was enough content-wise but tripped the SDK's
            # own "streaming required for long requests" guard - a
            # heuristic based purely on the max_tokens number, not the
            # actual response size. The real culprit is that max_tokens
            # covers thinking + response combined, and Sonnet 5's default
            # adaptive thinking makes that combined size unpredictable.
            # Disabling thinking removes the unpredictable half of the
            # budget entirely - the remaining max_tokens goes purely to
            # the structured JSON response, so a modest, fixed number is
            # both safely under the streaming threshold and still
            # generous for the response content alone.
            response = call_with_rate_limit_backoff(lambda: anthropic_client.messages.parse(
                model="claude-sonnet-5",
                max_tokens=16000,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=GeneratedPipeline,
            ))
            if response.parsed_output is None:
                # Structured outputs correctly refuses to return a result that
                # doesn't fully match the schema - most likely cause is the
                # response got cut off before finishing (a demanding goal,
                # or a long generated script, needing more than max_tokens).
                # stop_reason tells us which, rather than leaving this as an
                # opaque 'NoneType has no attribute model_dump' crash.
                raise RuntimeError(
                    f"Claude did not return a complete structured response "
                    f"(stop_reason={getattr(response, 'stop_reason', 'unknown')}). "
                    f"The goal may be too complex for the current token budget."
                )
            return response.parsed_output.model_dump()

        except Exception as e:
            print(f"[Warning] Anthropic pipeline generation failed: {e}")
            print("[Generator] Switching over to Fallback AI Provider: OpenAI (GPT-4o)...")

    # Fallback Attempt: OpenAI GPT-4o, via its own native structured outputs.
    if openai_client:
        try:
            response = openai_client.beta.chat.completions.parse(
                model="gpt-4o",
                response_format=GeneratedPipeline,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ]
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError(
                    response.choices[0].message.refusal
                    or "OpenAI declined to produce a structured response."
                )
            return parsed.model_dump()

        except Exception as e:
            raise RuntimeError(f"OpenAI fallback pipeline generation failed: {e}")

    raise ValueError("No AI provider is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")


if __name__ == "__main__":
    from introspect import introspect_schema

    print("Introspecting schema...")
    schema = introspect_schema()

    goal = input("Describe the pipeline you want (plain English): ")
    print("\nGenerating pipeline...\n")

    result = generate_pipeline(schema, goal)

    print(f"--- {result.get('pipeline_name', 'Generated Pipeline')} ---")
    print(result.get("description", "No description provided."))

    if result.get("assumptions"):
        print("\nAssumptions made:")
        for a in result["assumptions"]:
            print(f"  - {a}")

    out_path = "generated_pipeline.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result["code"])

    print(f"\nSaved to {out_path} — review before running against real data.")
