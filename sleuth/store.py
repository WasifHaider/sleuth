import json

import psycopg

from sleuth.chunking import Chunk


def _fetchone_or_none_on_bad_id(conn: psycopg.Connection, query: str, params: tuple):
    # Route path params (repo_id, chat_id, user_id) are plain strings until
    # Postgres parses them as uuid — a malformed one (not just "doesn't
    # exist", but not shaped like a UUID at all, e.g. a stray path segment
    # or a typo) raises psycopg.errors.InvalidTextRepresentation instead of
    # simply returning no rows. Every caller below already treats "no rows"
    # as its not-found case (returns None, and the route turns that into a
    # 404) — a malformed id should end up exactly there too, not surface as
    # a raw 500. Safe to catch broadly here: these are read-only SELECTs
    # under an autocommit connection, so a failed statement doesn't leave
    # the connection in an aborted-transaction state that later queries on
    # it would need a rollback to recover from.
    try:
        return conn.execute(query, params).fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        return None


def create_repo(conn: psycopg.Connection, github_url: str, user_id: str | None = None) -> str:
    row = conn.execute(
        "INSERT INTO repos (github_url, user_id) VALUES (%s, %s) RETURNING id", (github_url, user_id)
    ).fetchone()
    return str(row[0])


def list_repos(conn: psycopg.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute("SELECT id, github_url, status FROM repos ORDER BY github_url").fetchall()
    return [(str(repo_id), github_url, status) for repo_id, github_url, status in rows]


def list_repos_full(conn: psycopg.Connection, user_id: str) -> list[dict]:
    # Same shape as get_repo()'s row, but one query for every repo instead
    # of list_repos() + a get_repo() round trip per row (an N+1 query the
    # /repos route used to make — each extra round trip to a cross-region
    # Postgres, e.g. Supabase, costs ~200-400ms on its own, so N repos meant
    # N extra round trips just to re-fetch data the first query could have
    # returned already).
    #
    # Scoped to user_id: repos previously had no ownership at all, so every
    # authenticated user saw every repo anyone had ever connected — confirmed
    # live with two separate accounts both listing the identical repo set.
    rows = conn.execute(
        "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
        "FROM repos WHERE user_id = %s ORDER BY github_url",
        (user_id,),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "github_url": row[1],
            "status": row[2],
            "error_message": row[3],
            "embedding_model": row[4],
            "embedding_dim": row[5],
        }
        for row in rows
    ]


def get_repo(conn: psycopg.Connection, repo_id: str, user_id: str | None = None) -> dict | None:
    # user_id is optional (not every caller has one — the CLI has no auth
    # concept at all) but every web route DOES have one and MUST pass it:
    # omitting it here would defeat list_repos_full's ownership scoping by
    # letting anyone fetch any repo directly by id via GET /repos/{id}.
    if user_id is not None:
        row = _fetchone_or_none_on_bad_id(
            conn,
            "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
            "FROM repos WHERE id = %s AND user_id = %s",
            (repo_id, user_id),
        )
    else:
        row = _fetchone_or_none_on_bad_id(
            conn,
            "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
            "FROM repos WHERE id = %s",
            (repo_id,),
        )
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
                (repo_id, file_path, symbol_name, kind, start_line, end_line, code_text, content_hash, embedding, is_doc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_id, file_path, COALESCE(symbol_name, ''))
            DO UPDATE SET
                kind = EXCLUDED.kind,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                code_text = EXCLUDED.code_text,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding,
                is_doc = EXCLUDED.is_doc
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
                chunk.is_doc,
            ),
        )


def upsert_repo_summary(conn: psycopg.Connection, repo_id: str, summary: str) -> None:
    conn.execute(
        """
        INSERT INTO repo_summaries (repo_id, summary, generated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (repo_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            generated_at = EXCLUDED.generated_at
        """,
        (repo_id, summary),
    )


def get_repo_summary(conn: psycopg.Connection, repo_id: str) -> str | None:
    row = conn.execute(
        "SELECT summary FROM repo_summaries WHERE repo_id = %s", (repo_id,)
    ).fetchone()
    return row[0] if row is not None else None


def delete_repo(conn: psycopg.Connection, repo_id: str) -> None:
    # schema.sql wires chunks/repo_summaries/chats (and messages, cascading
    # off chats) all as ON DELETE CASCADE from repos — a single DELETE here
    # is enough to take the whole repo's data with it, no manual cleanup
    # of each child table needed.
    conn.execute("DELETE FROM repos WHERE id = %s", (repo_id,))


def delete_chat(conn: psycopg.Connection, chat_id: str) -> None:
    # messages.chat_id is ON DELETE CASCADE (schema.sql) — deleting the
    # chat row takes its messages with it automatically.
    conn.execute("DELETE FROM chats WHERE id = %s", (chat_id,))


def _user_row_to_dict(row) -> dict:
    user_id, email, password_hash, name, theme_preference, created_at = row
    return {
        "id": str(user_id),
        "email": email,
        "password_hash": password_hash,
        "name": name,
        "theme_preference": theme_preference,
        "created_at": created_at.isoformat(),
    }


class EmailAlreadyRegistered(Exception):
    pass


def create_user(conn: psycopg.Connection, email: str, password_hash: str, name: str | None) -> dict:
    # The pre-check (SELECT ... WHERE email = %s) is a UX nicety, not the
    # real guard against duplicates — two concurrent signups for the same
    # email can both pass it before either INSERTs (plain SELECT takes no
    # lock under READ COMMITTED), so the actual guarantee has to come from
    # the `users.email UNIQUE` constraint (schema.sql) and a caught
    # UniqueViolation, not from this check alone. Kept the pre-check too
    # since it avoids the round trip out to a failed INSERT for the common
    # case (spelled-out duplicate, no race), but the except below is what
    # actually makes the race safe.
    existing = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    if existing is not None:
        raise EmailAlreadyRegistered(email)
    try:
        row = conn.execute(
            """
            INSERT INTO users (email, password_hash, name)
            VALUES (%s, %s, %s)
            RETURNING id, email, password_hash, name, theme_preference, created_at
            """,
            (email, password_hash, name),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise EmailAlreadyRegistered(email)
    return _user_row_to_dict(row)


def get_user_by_email(conn: psycopg.Connection, email: str) -> dict | None:
    row = conn.execute(
        "SELECT id, email, password_hash, name, theme_preference, created_at FROM users WHERE email = %s",
        (email,),
    ).fetchone()
    if row is None:
        return None
    return _user_row_to_dict(row)


def get_user(conn: psycopg.Connection, user_id: str) -> dict | None:
    row = _fetchone_or_none_on_bad_id(
        conn,
        "SELECT id, email, password_hash, name, theme_preference, created_at FROM users WHERE id = %s",
        (user_id,),
    )
    if row is None:
        return None
    return _user_row_to_dict(row)


def set_user_theme(conn: psycopg.Connection, user_id: str, theme: str) -> None:
    conn.execute("UPDATE users SET theme_preference = %s WHERE id = %s", (theme, user_id))


DEFAULT_CHAT_TITLE = "New chat"


def create_chat(conn: psycopg.Connection, repo_id: str, title: str = DEFAULT_CHAT_TITLE) -> dict:
    row = conn.execute(
        "INSERT INTO chats (repo_id, title) VALUES (%s, %s) RETURNING id, created_at",
        (repo_id, title),
    ).fetchone()
    return {"id": str(row[0]), "title": title, "created_at": row[1].isoformat(), "message_count": 0}


def list_chats(conn: psycopg.Connection, repo_id: str) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, COUNT(m.id)
            FROM chats c LEFT JOIN messages m ON m.chat_id = c.id
            WHERE c.repo_id = %s
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """,
            (repo_id,),
        ).fetchall()
    except psycopg.errors.InvalidTextRepresentation:
        # A malformed repo_id (not a real query-string validation error,
        # just not shaped like a uuid) can't match any chats — same
        # not-found-shaped outcome as a well-formed id with zero results,
        # rather than a raw 500.
        return []
    return [
        {"id": str(cid), "title": title, "created_at": created_at.isoformat(), "message_count": count}
        for cid, title, created_at, count in rows
    ]


def get_chat(conn: psycopg.Connection, chat_id: str) -> dict | None:
    row = _fetchone_or_none_on_bad_id(
        conn, "SELECT id, repo_id, title FROM chats WHERE id = %s", (chat_id,)
    )
    if row is None:
        return None
    return {"id": str(row[0]), "repo_id": str(row[1]), "title": row[2]}


_TITLE_MAX_LEN = 60


def derive_chat_title(question: str, max_len: int = _TITLE_MAX_LEN) -> str:
    """First-message-becomes-the-title: a single-line, truncated version of
    the question the user actually asked, so the sidebar/header show
    something recognizable instead of every chat reading "New chat" forever."""
    collapsed = " ".join(question.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len].rstrip() + "…"


def update_chat_title(conn: psycopg.Connection, chat_id: str, title: str) -> None:
    conn.execute("UPDATE chats SET title = %s WHERE id = %s", (title, chat_id))


def create_message(
    conn: psycopg.Connection, chat_id: str, role: str, content: str, sources: list[dict] | None = None
) -> str:
    row = conn.execute(
        "INSERT INTO messages (chat_id, role, content, sources) VALUES (%s, %s, %s, %s) RETURNING id",
        (chat_id, role, content, json.dumps(sources) if sources is not None else None),
    ).fetchone()
    return str(row[0])


def list_messages(conn: psycopg.Connection, chat_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, sources, created_at FROM messages WHERE chat_id = %s ORDER BY created_at",
        (chat_id,),
    ).fetchall()
    return [
        {"id": str(mid), "role": role, "content": content, "sources": sources, "created_at": created_at.isoformat()}
        for mid, role, content, sources, created_at in rows
    ]


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
