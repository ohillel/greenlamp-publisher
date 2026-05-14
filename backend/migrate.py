"""
Run database migrations against the Supabase PostgreSQL instance.

Requires SUPABASE_DB_URL in backend/.env:
  SUPABASE_DB_URL=postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres

Find this in: Supabase Dashboard → Settings → Database → Connection string (URI)
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "schema.sql")


def run():
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL is not set in backend/.env")
        print()
        print("Add it like this:")
        print("  SUPABASE_DB_URL=postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres")
        print()
        print("Find the connection string at:")
        print("  Supabase Dashboard → Settings → Database → Connection string (URI)")
        sys.exit(1)

    with open(SCHEMA_FILE) as f:
        sql = f.read()

    print("Connecting to Supabase PostgreSQL…")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    print("Running schema.sql…")
    cur.execute(sql)
    cur.close()
    conn.close()
    print("Done — tables created / verified.")


if __name__ == "__main__":
    run()
