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

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.environ.get("PG_PORT", 5432),
        dbname=os.environ["PG_DATABASE"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )


def introspect_schema(schema_name: str = "public", sample_rows: int = 3) -> dict:
    """
    Returns: { table_name: { columns: [...], row_count: int, sample: [...] } }

    sample_rows=0 disables sample-row collection entirely, useful once you're
    working with real customer data and want schema shape only, no content.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position;
        """,
        (schema_name,),
    )

    tables: dict = {}
    for row in cur.fetchall():
        t = row["table_name"]
        tables.setdefault(t, {"columns": [], "row_count": None, "sample": []})
        tables[t]["columns"].append(
            {
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
            }
        )

    for table_name in tables:
        count_query = sql.SQL("SELECT COUNT(*) AS c FROM {}.{}").format(
            sql.Identifier(schema_name), sql.Identifier(table_name)
        )
        cur.execute(count_query)
        tables[table_name]["row_count"] = cur.fetchone()["c"]

        if sample_rows > 0:
            sample_query = sql.SQL("SELECT * FROM {}.{} LIMIT %s").format(
                sql.Identifier(schema_name), sql.Identifier(table_name)
            )
            cur.execute(sample_query, (sample_rows,))
            tables[table_name]["sample"] = cur.fetchall()

    cur.close()
    conn.close()
    return tables


if __name__ == "__main__":
    schema = introspect_schema()
    print(json.dumps(schema, indent=2, default=str))
