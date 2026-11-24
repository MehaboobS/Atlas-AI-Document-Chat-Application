"""
Initialize Atlas tables in Render PostgreSQL from infra/schema.sql.

Usage (from project root):
    python scripts/init_db.py

Safe to rerun: the supplied schema uses CREATE ... IF NOT EXISTS.
"""

from pathlib import Path
import sys
import os
import psycopg
from dotenv import load_dotenv

# Resolve project root and load environment variables
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SCHEMA_FILE = ROOT / "infra" / "schema.sql"
REQUIRED_TABLES = (
    "documents",
    "feedback",
    "audit_events",
    "approvals",
    "chat_messages",
)


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url or "YOUR_" in url or "USERNAME:" in url:
        print("ERROR: Set a real DATABASE_URL in the project .env file.")
        return 1

    if not SCHEMA_FILE.is_file():
        print(f"ERROR: Schema file not found at: {SCHEMA_FILE}")
        return 1

    print("Connecting to Render PostgreSQL (credentials masked)...")
    try:
        with psycopg.connect(url, connect_timeout=15) as conn:
            # Execute as a single atomic transaction: either all succeeds or rolls back
            with conn.transaction():
                sql_content = SCHEMA_FILE.read_text(encoding="utf-8")
                conn.execute(sql_content)

            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), version()")
                db_info = cur.fetchone()
                dbname = db_info[0] if db_info else "unknown"

                # Verify all required tables exist in public schema
                missing = []
                for table in REQUIRED_TABLES:
                    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                    result = cur.fetchone()
                    if result is None or result[0] is None:
                        missing.append(table)

                if missing:
                    raise RuntimeError(f"Required table(s) not created: {missing}")

        print(f"SUCCESS: Database '{dbname}' initialized successfully!")
        print(f"Verified {len(REQUIRED_TABLES)} tables: {', '.join(REQUIRED_TABLES)}")
        print("Safe to rerun: existing tables and rows are preserved.")
        return 0

    except Exception as exc:
        print(f"ERROR: Database initialization failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
