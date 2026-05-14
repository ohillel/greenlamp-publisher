"""
Deletes and recreates the three application users via Supabase Auth Admin API.
Requires SUPABASE_SERVICE_ROLE_KEY in backend/.env.
"""
import os
import psycopg2
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

USERS = [
    {"email": "denise@greenlamp.co",      "password": "Greenlamp1!", "role": "denise"},
    {"email": "seojobisrael@gmail.com",   "password": "Greenlamp1!", "role": "or"},
    {"email": "office@greenlamp.co",      "password": "Greenlamp1!", "role": "publisher"},
]


def run():
    url      = os.getenv("SUPABASE_URL")
    svc_key  = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    db_url   = os.getenv("SUPABASE_DB_URL")

    if not svc_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set in .env")

    # Admin client (service role bypasses RLS + can manage auth users)
    admin = create_client(url, svc_key)

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    for u in USERS:
        email    = u["email"]
        password = u["password"]
        role     = u["role"]

        # ── 1. Delete existing user if present ──────────────────────────────
        cur.execute("SELECT id FROM auth.users WHERE email = %s", [email])
        row = cur.fetchone()
        if row:
            old_id = str(row[0])
            admin.auth.admin.delete_user(old_id)
            print(f"[deleted]  {email}  ({old_id})")

        # ── 2. Create via Admin API (handles hashing, confirmation, metadata) ─
        response = admin.auth.admin.create_user({
            "email":            email,
            "password":         password,
            "email_confirm":    True,   # mark email as already confirmed
        })
        user_id = response.user.id
        print(f"[created]  {email}  ({user_id})")

        # ── 3. Upsert profile with role ──────────────────────────────────────
        cur.execute("""
            INSERT INTO public.profiles (id, email, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET role  = EXCLUDED.role,
                    email = EXCLUDED.email
        """, [user_id, email, role])
        print(f"           role = {role}")

    cur.close()
    conn.close()
    print("\nAll users recreated successfully.")
    print("Password for all users: Greenlamp1!")


if __name__ == "__main__":
    run()
