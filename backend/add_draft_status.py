"""
Add 'draft' to the articles.status CHECK constraint and enable RLS on
the notifications table so Denise's "Confirm & Notify Or" flow can insert rows.
"""
import os, psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
conn = psycopg2.connect(os.environ['SUPABASE_DB_URL'])
conn.autocommit = True
cur = conn.cursor()

sql = """
-- 1. Drop existing CHECK constraint and recreate with 'draft' added
ALTER TABLE public.articles
  DROP CONSTRAINT IF EXISTS articles_status_check;

ALTER TABLE public.articles
  ADD CONSTRAINT articles_status_check
  CHECK (status IN ('draft', 'submitted', 'approved', 'sent_to_publisher'));

-- 2. Enable RLS on notifications and add open authenticated policy
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE pol RECORD;
BEGIN
  FOR pol IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'notifications'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.notifications', pol.policyname);
  END LOOP;
END $$;

CREATE POLICY "notifications_authenticated_all"
  ON public.notifications
  FOR ALL
  TO authenticated
  USING (true)
  WITH CHECK (true);
"""

print("Applying migration…")
cur.execute(sql)
print("Done.")

# Verify
cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'public.articles'::regclass AND contype = 'c'")
for row in cur.fetchall():
    print(f"  constraint: {row[0]} → {row[1]}")

cur.close()
conn.close()
