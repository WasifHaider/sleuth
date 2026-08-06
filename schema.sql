CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS repos (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_url    text NOT NULL,
    status        text NOT NULL DEFAULT 'pending',
    error_message text,
    indexed_at    timestamptz
);

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
