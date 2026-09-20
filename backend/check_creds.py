"""One-off diagnostic: decrypts and prints a connection profile's actual
stored credentials, rather than guessing from what was typed in the UI
or what the profile's display name suggests. Run from backend/ with the
venv active - it reuses connection_service.py's own cipher, so it's
reading the credentials exactly the way the app itself does.

Usage: python check_creds.py "Events Source (Bronze)"
"""
import json
import sys

import sqlalchemy as sa
from dotenv import load_dotenv

load_dotenv(override=False)

from connection_service import credential_cipher  # noqa: E402  (needs load_dotenv first)


def main():
    if len(sys.argv) != 2:
        print('Usage: python check_creds.py "Profile Name"')
        sys.exit(1)

    profile_name = sys.argv[1]

    import os
    db_url = os.environ.get("DATABASE_URL") or "postgresql://pulse:pulse_secure_password@localhost:5433/pulse_audit"
    engine = sa.create_engine(db_url)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT type, encrypted_credentials FROM connection_profiles WHERE name = :name"),
            {"name": profile_name},
        ).fetchone()

    if row is None:
        print(f"No connection profile found named {profile_name!r}.")
        sys.exit(1)

    conn_type, encrypted = row
    decrypted = credential_cipher().decrypt(bytes(encrypted)).decode("utf-8")
    credentials = json.loads(decrypted)

    print(f"type: {conn_type}")
    print(json.dumps(credentials, indent=2))

    if conn_type == "postgres":
        schema_value = credentials.get("schema")
        print(f"\nresolved schema (what postgres_schema_name() would return): {schema_value or 'public'!r}")


if __name__ == "__main__":
    main()
