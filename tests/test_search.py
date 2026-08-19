from sleuth.chunking import Chunk
from sleuth.retrieve.search import search_chunks
from sleuth.store import create_repo, upsert_chunks


def test_search_returns_closest_chunk_first(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    near = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    far = Chunk("g.py", "bar", "function", 1, 2, "def bar():\n    return 2\n")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(near, [1.0] + [0.0] * 1023), (far, [0.0] * 1023 + [1.0])],
    )
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, [1.0] + [0.0] * 1023, top_k=8)

    assert results[0].symbol_name == "foo"
    assert results[0].distance < results[1].distance


def test_search_respects_top_k(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunks = [
        Chunk(f"f{i}.py", f"sym{i}", "function", 1, 2, f"code {i}")
        for i in range(5)
    ]
    upsert_chunks(pg_conn, repo_id, [(c, [float(i)] * 1024) for i, c in enumerate(chunks)])
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, [0.0] * 1024, top_k=2)

    assert len(results) == 2


def test_search_scopes_to_repo_id(pg_conn):
    repo_a = create_repo(pg_conn, "https://github.com/example/repo-a")
    repo_b = create_repo(pg_conn, "https://github.com/example/repo-b")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "code a")
    chunk_b = Chunk("f.py", "foo", "function", 1, 2, "code b")

    upsert_chunks(pg_conn, repo_a, [(chunk_a, [1.0] * 1024)])
    upsert_chunks(pg_conn, repo_b, [(chunk_b, [1.0] * 1024)])
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_a, [1.0] * 1024, top_k=8)

    assert len(results) == 1
    assert results[0].code_text == "code a"
