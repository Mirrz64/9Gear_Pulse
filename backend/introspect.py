"""
Schema introspection for Postgres — Phase 1 of the AI-to-pipeline wedge.

Connects using credentials from environment variables only. Returns a
compact JSON summary of tables/columns/types/row counts/sample rows —
this summary is the ONLY thing that ever gets handed to the AI. The raw
connection credentials never leave this script.
"""
import os
import json
from dotenv import load_dotenv
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

load_dotenv(override=True)  # Load .env file, but allow real env vars to override


def get_connection():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ.get("PG_PORT", 5433)),
        dbname=os.environ["PG_DATABASE"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )


def introspect_schema(schema_name: str = None, sample_rows: int = 3) -> dict:
    """
    Returns: { full_table_name: { columns: [...], row_count: int, sample: [...] } }

    If schema_name is None, introspects all non-system schemas in the database.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Filter out system schemas by default
    if schema_name:
        schema_filter = "WHERE table_schema = %s"
        params = (schema_name,)
    else:
        schema_filter = "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        params = ()

    cur.execute(
        f"""
        SELECT table_schema, table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        {schema_filter}
        ORDER BY table_schema, table_name, ordinal_position;
        """,
        params,
    )

    tables: dict = {}
    for row in cur.fetchall():
        s_name = row["table_schema"]
        t_name = row["table_name"]
        # Use fully-qualified schema.table format key
        full_table_key = f"{s_name}.{t_name}"

        tables.setdefault(full_table_key, {
            "schema": s_name,
            "table": t_name,
            "columns": [],
            "row_count": None,
            "sample": []
        })
        tables[full_table_key]["columns"].append(
            {
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
            }
        )

    # Gather row counts and sample rows for each discovered table
    for full_key, details in tables.items():
        s = details["schema"]
        t = details["table"]

        count_query = sql.SQL("SELECT COUNT(*) AS c FROM {}.{}").format(
            sql.Identifier(s), sql.Identifier(t)
        )
        try:
            cur.execute(count_query)
            tables[full_key]["row_count"] = cur.fetchone()["c"]

            if sample_rows > 0:
                sample_query = sql.SQL("SELECT * FROM {}.{} LIMIT %s").format(
                    sql.Identifier(s), sql.Identifier(t)
                )
                cur.execute(sample_query, (sample_rows,))
                tables[full_key]["sample"] = cur.fetchall()
        except Exception as e:
            # Skip tables with restricted permissions or transient locks
            tables[full_key]["row_count"] = 0
            tables[full_key]["sample"] = []

    cur.close()
    conn.close()
    return tables


if __name__ == "__main__":
    schema = introspect_schema()
    print(json.dumps(schema, indent=2, default=str))