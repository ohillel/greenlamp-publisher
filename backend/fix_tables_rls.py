"""
Ensure articles and clients tables have RLS enabled with a simple
"any authenticated user can do everything" policy.
Without this, if RLS is accidentally enabled with no policies,
all queries silently return 0 rows.
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
db_url = os.environ['SUPABASE_DB_URL']

sql = """
-- ── articles ──────────────────────────────────────────────────────────────
ALTER TABLE public.articles ENABLE ROW LEVEL SECURITY;

-- Drop any existing policies so we start clean
DO $$
DECLARE pol RECORD;
BEGIN
  FOR pol IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'articles'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.articles', pol.policyname);
  END LOOP;
END $$;

-- All authenticated users can read / write articles (internal tool, auth is the gate)
CREATE POLICY "articles_authenticated_all"
  ON public.articles
  FOR ALL
  TO authenticated
  USING (true)
  WITH CHECK (true);

-- ── clients ───────────────────────────────────────────────────────────────
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE pol RECORD;
BEGIN
  FOR pol IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'clients'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.clients', pol.policyname);
  END LOOP;
END $$;

CREATE POLICY "clients_authenticated_all"
  ON public.clients
  FOR ALL
  TO authenticated
  USING (true)
  WITH CHECK (true);
"""

print("Connecting…")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

print("Applying RLS policies to articles + clients…")
cur.execute(sql)
print("Done.\n")

for table in ('articles', 'clients'):
    cur.execute("""
        SELECT policyname, roles, cmd
        FROM pg_policies
        WHERE schemaname = 'public' AND tablename = %s
        ORDER BY policyname
    """, (table,))
    rows = cur.fetchall()
    print(f"public.{table} — {len(rows)} policy/policies:")
    for r in rows:
        print(f"  {r[0]!r:40s}  roles={r[1]}  cmd={r[2]}")
    print()

cur.close()
conn.close()
