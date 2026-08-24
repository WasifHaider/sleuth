import psycopg

from sleuth.chunking import Chunk


def create_repo(conn: psycopg.Connection, github_url: str) -> str:
    row = conn.execute(
        "INSERT INTO repos (github_url) VALUES (%s) RETURNING id", (github_url,)
    ).fetchone()
    return str(row[0])


def list_repos(conn: psycopg.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute("SELECT id, github_url, status FROM repos ORDER BY github_url").fetchall()
    return [(str(repo_id), github_url, status) for repo_id, github_url, status in rows]


def get_repo(conn: psycopg.Connection, repo_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
        "FROM repos WHERE id = %s",
        (repo_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "github_url": row[1],
        "status": row[2],
        "error_message": row[3],
        "embedding_model": row[4],
        "embedding_dim": row[5],
    }


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


def _user_row_to_dict(row) -> dict:
    user_id, github_id, email, name, avatar_url, theme_preference, created_at = row
    return {
        "id": str(user_id),
        "github_id": github_id,
        "email": email,
        "name": name,
        "avatar_url": avatar_url,
        "theme_preference": theme_preference,
        "created_at": created_at.isoformat(),
    }


def get_or_create_user_by_github(
    conn: psycopg.Connection, github_id: int, email: str | None, name: str | None, avatar_url: str | None
) -> dict:
    row = conn.execute(
        """
        INSERT INTO users (github_id, email, name, avatar_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (github_id) DO UPDATE SET
            email = EXCLUDED.email,
            name = EXCLUDED.name,
            avatar_url = EXCLUDED.avatar_url
        RETURNING id, github_id, email, name, avatar_url, theme_preference, created_at
        """,
        (github_id, email, name, avatar_url),
    ).fetchone()
    return _user_row_to_dict(row)


def get_or_create_user_by_email(conn: psycopg.Connection, email: str) -> dict:
    row = conn.execute(
        """
        INSERT INTO users (email)
        VALUES (%s)
        ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
        RETURNING id, github_id, email, name, avatar_url, theme_preference, created_at
        """,
        (email,),
    ).fetchone()
    return _user_row_to_dict(row)


def get_user(conn: psycopg.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, github_id, email, name, avatar_url, theme_preference, created_at "
        "FROM users WHERE id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return _user_row_to_dict(row)


def set_user_theme(conn: psycopg.Connection, user_id: str, theme: str) -> None:
    conn.execute("UPDATE users SET theme_preference = %s WHERE id = %s", (theme, user_id))


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
