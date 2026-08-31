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

-- Marks chunks from files under a docs/doc/documentation directory (see
-- sleuth/chunking.py::is_doc_path) — hand-written architecture/status
-- write-ups that happen to be real, parseable .html/.md files get chunked
-- and embedded exactly like actual source, and a prose write-up about "the
-- auth flow" can out-score the real auth code on raw cosine similarity
-- against an architecture-flavored question. search_chunks uses this to
-- always rank real code ahead of documentation instead of treating both as
-- equally "the codebase". Backfilled false for any already-ingested repo;
-- those rows are only actually correct again after the NEXT re-index (the
-- API's "Retry indexing" action, or CLI `sleuth add` against the same URL).
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS is_doc boolean NOT NULL DEFAULT false;

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

-- repos/chats/messages had no per-user ownership at all — any authenticated
-- user could list, read, or chat against ANY other user's repos simply by
-- knowing (or guessing, since ids leak into API responses/URLs) a UUID;
-- confirmed live, two separate signups both saw the identical global repo
-- list. Nullable so this applies cleanly to any DB that already has rows
-- from before this column existed — application code always sets it on
-- create from here on, existing NULL rows are the one-time migration gap,
-- not the steady state. Placed after CREATE TABLE users (not next to the
-- other repos ALTERs above, near the top of this file) because the FK
-- target has to already exist for this to run on a fresh database.
ALTER TABLE repos ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS repos_user_id_idx ON repos (user_id);

CREATE TABLE IF NOT EXISTS chats (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id    uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    title      text NOT NULL DEFAULT 'New chat',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id    uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,
    sources    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_chat_idx ON messages (chat_id, created_at);

-- Global/architecture-question retrieval (2026-08-30): a single row per
-- repo holding a cheap LLM-generated "what is this repo" summary, built
-- from a repo-map of file/symbol/kind (not full source text) during
-- ingest. Not modeled as a chunks row (no code span, no vector search
-- role — it's read back whole, not retrieved by cosine distance) and not
-- an ALTER on chunks, since a summary isn't "a piece of the codebase" the
-- way a chunk is. One-to-one with repos: overwritten wholesale on every
-- re-index (see upsert_repo_summary), never versioned/append-only.
CREATE TABLE IF NOT EXISTS repo_summaries (
    repo_id      uuid PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
    summary      text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now()
);
