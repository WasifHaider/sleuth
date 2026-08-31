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


def test_search_ranks_code_ahead_of_docs_even_when_docs_are_closer(pg_conn):
    # The actual bug this guards: docs/*.html architecture write-ups are
    # real, parseable files that can score a CLOSER cosine match than the
    # real implementation against an architecture-flavored question — left
    # unranked, that silently crowds the real code out of the top-k results
    # an LLM is ever shown.
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    doc_chunk = Chunk("docs/auth-architecture.html", None, "element", 1, 5, "<p>Auth uses JWT...</p>")
    code_chunk = Chunk("sleuth/api/auth/session.py", "sign_session", "function", 1, 10, "def sign_session(): ...")

    # doc_chunk is embedded CLOSER to the query vector than code_chunk —
    # on raw distance alone it would rank first.
    upsert_chunks(
        pg_conn,
        repo_id,
        [(doc_chunk, [1.0] + [0.0] * 1023), (code_chunk, [0.9] + [0.1] + [0.0] * 1022)],
    )
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, [1.0] + [0.0] * 1023, top_k=8)

    assert results[0].file_path == "sleuth/api/auth/session.py"
    assert results[0].is_doc is False
    assert results[1].file_path == "docs/auth-architecture.html"
    assert results[1].is_doc is True


def test_search_prefer_code_false_falls_back_to_raw_distance(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    doc_chunk = Chunk("docs/auth-architecture.html", None, "element", 1, 5, "<p>Auth uses JWT...</p>")
    code_chunk = Chunk("sleuth/api/auth/session.py", "sign_session", "function", 1, 10, "def sign_session(): ...")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(doc_chunk, [1.0] + [0.0] * 1023), (code_chunk, [0.9] + [0.1] + [0.0] * 1022)],
    )
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, [1.0] + [0.0] * 1023, top_k=8, prefer_code=False)

    assert results[0].file_path == "docs/auth-architecture.html"
