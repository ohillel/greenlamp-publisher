import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

db_url = os.environ['SUPABASE_DB_URL']

sql = """
-- Drop all existing policies on profiles
DO $$
DECLARE
    pol RECORD;
BEGIN
    FOR pol IN
        SELECT policyname
        FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'profiles'
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.profiles', pol.policyname);
    END LOOP;
END
$$;

-- Make sure RLS is enabled
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Authenticated users can read their own profile
CREATE POLICY "profiles_select_own"
  ON public.profiles
  FOR SELECT
  TO authenticated
  USING (auth.uid() = id);

-- Service role bypasses RLS by default in Supabase, but add an explicit
-- all-access policy so anon key + service role reads also work correctly.
CREATE POLICY "profiles_service_role_all"
  ON public.profiles
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
"""

print("Connecting to Supabase DB…")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

print("Running RLS migration…")
cur.execute(sql)
print("Done.")

# Verify
cur.execute("""
    SELECT policyname, roles, cmd
    FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'profiles'
    ORDER BY policyname;
""")
rows = cur.fetchall()
print(f"\nActive policies on public.profiles ({len(rows)}):")
for row in rows:
    print(f"  {row[0]!r:40s}  roles={row[1]}  cmd={row[2]}")

cur.close()
conn.close()
