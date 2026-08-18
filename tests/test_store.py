from sleuth.chunking import Chunk
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
)


def test_create_repo_and_update_status(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    row = pg_conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "pending"

    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()

    row = pg_conn.execute("SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "ready"
    assert row[1] is None


def test_update_repo_status_with_error_message(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    update_repo_status(pg_conn, repo_id, "failed", error_message="clone timed out")
    pg_conn.commit()

    row = pg_conn.execute("SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "failed"
    assert row[1] == "clone timed out"


def test_set_repo_embedding_info(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    set_repo_embedding_info(pg_conn, repo_id, "voyage-code-3", 1024)
    pg_conn.commit()

    row = pg_conn.execute(
        "SELECT embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "voyage-code-3"
    assert row[1] == 1024


def test_upsert_and_get_existing_hashes(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    chunk_b = Chunk("f.py", None, "module", 3, 3, "X = 1\n")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)],
    )
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id)

    assert hashes[("f.py", "foo")] == chunk_a.content_hash
    assert hashes[("f.py", None)] == chunk_b.content_hash


def test_upsert_overwrites_existing_row_on_conflict(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    original = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    upsert_chunks(pg_conn, repo_id, [(original, [0.1] * 1024)])
    pg_conn.commit()

    changed = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 2\n")
    upsert_chunks(pg_conn, repo_id, [(changed, [0.9] * 1024)])
    pg_conn.commit()

    count = pg_conn.execute("SELECT count(*) FROM chunks WHERE repo_id = %s", (repo_id,)).fetchone()[0]
    assert count == 1

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert hashes[("f.py", "foo")] == changed.content_hash


def test_delete_stale_chunks_removes_missing_keys(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "code a")
    chunk_b = Chunk("g.py", "bar", "function", 1, 2, "code b")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)],
    )
    pg_conn.commit()

    delete_stale_chunks(pg_conn, repo_id, current_keys={("f.py", "foo")})
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert set(hashes.keys()) == {("f.py", "foo")}
