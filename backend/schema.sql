-- greenlamp-publisher schema

CREATE TABLE IF NOT EXISTS clients (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS articles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    google_doc_url      TEXT,
    magazine            TEXT,
    preferred_publisher TEXT,
    chosen_publisher    TEXT,
    price_presswhizz    NUMERIC(10, 2),
    price_linksme       NUMERIC(10, 2),
    status              TEXT NOT NULL DEFAULT 'submitted'
                            CHECK (status IN ('draft', 'submitted', 'approved', 'sent_to_publisher', 'published', 'not_published')),
    publisher_notes     TEXT,
    return_reason       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS articles_set_updated_at ON articles;
CREATE TRIGGER articles_set_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_role   TEXT NOT NULL CHECK (user_role IN ('denise', 'or', 'publisher')),
    article_id  UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    message     TEXT NOT NULL,
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS profiles (
    id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('denise', 'or', 'publisher')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS: authenticated users can only read their own profile
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "profiles_select_own" ON profiles;
CREATE POLICY "profiles_select_own" ON profiles
    FOR SELECT USING (auth.uid() = id);

-- Indexes
CREATE INDEX IF NOT EXISTS articles_client_id_idx        ON articles(client_id);
CREATE INDEX IF NOT EXISTS articles_status_idx           ON articles(status);
CREATE INDEX IF NOT EXISTS notifications_user_role_idx   ON notifications(user_role);
CREATE INDEX IF NOT EXISTS notifications_article_id_idx  ON notifications(article_id);
CREATE INDEX IF NOT EXISTS notifications_is_read_idx     ON notifications(is_read);
CREATE INDEX IF NOT EXISTS profiles_role_idx             ON profiles(role);

-- ── Migration: add publisher_notes and return_reason columns ─────────────────
-- Run these once against the live Supabase DB (safe to run multiple times):
ALTER TABLE articles ADD COLUMN IF NOT EXISTS publisher_notes TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS return_reason   TEXT;

-- ── Migration: add published_url column ──────────────────────────────────────
ALTER TABLE articles ADD COLUMN IF NOT EXISTS published_url TEXT;

-- ── Migration: add google_doc_url to clients ─────────────────────────────────
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_doc_url TEXT;

-- ── Migration: add reminder_sent column ──────────────────────────────────────
ALTER TABLE articles ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE;

-- ── Migration: add published_at column ───────────────────────────────────────
ALTER TABLE articles ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
