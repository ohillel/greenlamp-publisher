"""Add articles table to Supabase realtime publication."""
import os, psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
conn = psycopg2.connect(os.environ['SUPABASE_DB_URL'])
conn.autocommit = True
cur = conn.cursor()

cur.execute("ALTER PUBLICATION supabase_realtime ADD TABLE public.articles;")
print("articles table added to supabase_realtime publication.")

cur.close()
conn.close()
