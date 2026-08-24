CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS repos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_url      text NOT NULL,
    status          text NOT NULL DEFAULT 'pending',
    error_message   text,
    indexed_at      timestamptz,
    embedding_model text,
    embedding_dim   int
);

ALTER TABLE repos ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE repos ADD COLUMN IF NOT EXISTS embedding_dim int;

CREATE TABLE IF NOT EXISTS chunks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id       uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_path     text NOT NULL,
    symbol_name   text,
    kind          text NOT NULL,
    start_line    int NOT NULL,
    end_line      int NOT NULL,
    code_text     text NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(1024) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_identity_idx
    ON chunks (repo_id, file_path, COALESCE(symbol_name, ''));

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           text UNIQUE,
    password_hash   text,
    name            text,
    theme_preference text NOT NULL DEFAULT 'storm',
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Auth switched from GitHub OAuth + email magic-link to email+password
-- (2026-08-24 decision) before the OAuth/magic-link columns ever shipped to
-- a real user — drop the now-dead GitHub-only columns from any DB that
-- already applied the old schema. email/password_hash are left nullable at
-- the column level (existing rows, if any, predate password_hash) but the
-- signup path always sets both.
ALTER TABLE users DROP COLUMN IF EXISTS github_id;
ALTER TABLE users DROP COLUMN IF EXISTS avatar_url;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
