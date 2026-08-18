import psycopg

from sleuth.chunking import Chunk


def create_repo(conn: psycopg.Connection, github_url: str) -> str:
    row = conn.execute(
        "INSERT INTO repos (github_url) VALUES (%s) RETURNING id", (github_url,)
    ).fetchone()
    return str(row[0])


def update_repo_status(
    conn: psycopg.Connection, repo_id: str, status: str, error_message: str | None = None
) -> None:
    conn.execute(
        "UPDATE repos SET status = %s, error_message = %s WHERE id = %s",
        (status, error_message, repo_id),
    )


def set_repo_embedding_info(conn: psycopg.Connection, repo_id: str, model: str, dim: int) -> None:
    conn.execute(
        "UPDATE repos SET embedding_model = %s, embedding_dim = %s WHERE id = %s",
        (model, dim, repo_id),
    )


def get_existing_hashes(conn: psycopg.Connection, repo_id: str) -> dict[tuple[str, str | None], str]:
    rows = conn.execute(
        "SELECT file_path, symbol_name, content_hash FROM chunks WHERE repo_id = %s", (repo_id,)
    ).fetchall()
    return {(file_path, symbol_name): content_hash for file_path, symbol_name, content_hash in rows}


def upsert_chunks(
    conn: psycopg.Connection,
    repo_id: str,
    chunks_with_embeddings: list[tuple[Chunk, list[float]]],
) -> None:
    for chunk, embedding in chunks_with_embeddings:
        conn.execute(
            """
            INSERT INTO chunks
                (repo_id, file_path, symbol_name, kind, start_line, end_line, code_text, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_id, file_path, COALESCE(symbol_name, ''))
            DO UPDATE SET
                kind = EXCLUDED.kind,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                code_text = EXCLUDED.code_text,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding
            """,
            (
                repo_id,
                chunk.file_path,
                chunk.symbol_name,
                chunk.kind,
                chunk.start_line,
                chunk.end_line,
                chunk.code_text,
                chunk.content_hash,
                embedding,
            ),
        )


def delete_stale_chunks(
    conn: psycopg.Connection, repo_id: str, current_keys: set[tuple[str, str | None]]
) -> None:
    existing = get_existing_hashes(conn, repo_id)
    stale_keys = existing.keys() - current_keys
    for file_path, symbol_name in stale_keys:
        conn.execute(
            "DELETE FROM chunks WHERE repo_id = %s AND file_path = %s AND COALESCE(symbol_name, '') = COALESCE(%s, '')",
            (repo_id, file_path, symbol_name),
        )
