import json

import httpx
import pytest
import respx

from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.retrieve.answer import build_prompt, get_answer, stream_answer
from sleuth.retrieve.search import SearchResult
from sleuth.store import create_repo, set_repo_embedding_info, update_repo_status, upsert_chunks


def test_build_prompt_includes_question_and_chunks():
    results = [
        SearchResult("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n", 0.1),
        SearchResult("g.py", None, "module", 1, 1, "X = 1\n", 0.2),
    ]

    prompt = build_prompt("What does foo do?", results)

    assert "What does foo do?" in prompt
    assert "f.py" in prompt
    assert "foo" in prompt
    assert "def foo():" in prompt
    assert "g.py" in prompt
    assert "X = 1" in prompt


def test_build_prompt_labels_doc_vs_code_excerpts():
    results = [
        SearchResult("sleuth/auth.py", "sign", "function", 1, 2, "def sign(): ...", 0.1, is_doc=False),
        SearchResult("docs/auth.html", None, "element", 1, 2, "<p>Auth uses JWT</p>", 0.2, is_doc=True),
    ]

    prompt = build_prompt("How does auth work?", results)

    assert "[CODE] File: sleuth/auth.py" in prompt
    assert "[DOCUMENTATION] File: docs/auth.html" in prompt


def test_build_prompt_prepends_summary_for_global_questions():
    prompt = build_prompt("Rate my architecture", [], summary="This is a RAG chatbot.")
    assert prompt.index("This is a RAG chatbot.") < prompt.index("Relevant excerpts")
    assert "REPO SUMMARY" in prompt


def test_build_prompt_omits_summary_block_when_none():
    prompt = build_prompt("Where is X?", [])
    assert "REPO SUMMARY" not in prompt


@pytest.mark.asyncio
@respx.mock
async def test_get_answer_rejects_repo_not_ready(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()  # status defaults to 'pending'
    config = Config(
        voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused",
    )

    with pytest.raises(ValueError, match="not ready"):
        await get_answer("question?", repo_id, pg_conn, config)

    assert len(respx.calls) == 0


@pytest.mark.asyncio
@respx.mock
async def test_get_answer_embeds_question_and_streams_generated_answer(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    set_repo_embedding_info(pg_conn, repo_id, "voyage-code-3", 1024)
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert any("foo" in m["content"] for m in body["messages"])
        sse = 'data: {"choices":[{"delta":{"content":"foo returns 1."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(
        voyage_api_key="k", groq_api_key="k", groq_model="test-model", database_url="unused",
    )
    answer = await get_answer("What does foo do?", repo_id, pg_conn, config)

    assert answer == "foo returns 1."
    voyage_calls = [c for c in respx.calls if "voyageai" in str(c.request.url)]
    assert len(voyage_calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_reports_sources_via_callback(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
    )

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    captured = []
    tokens = [
        t async for t in stream_answer(
            "q?", repo_id, pg_conn, config, on_sources=lambda results: captured.append(results)
        )
    ]

    assert "".join(tokens) == "hi"
    assert len(captured) == 1
    assert captured[0][0].file_path == "f.py"


@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_includes_stored_summary_for_global_question(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    from sleuth.store import upsert_repo_summary
    upsert_repo_summary(pg_conn, repo_id, "This repo is a small RAG chatbot backend.")
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert any("This repo is a small RAG chatbot backend." in m["content"] for m in body["messages"])
        sse = 'data: {"choices":[{"delta":{"content":"It is a RAG chatbot."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    answer = await get_answer("Rate my overall architecture", repo_id, pg_conn, config)

    assert answer == "It is a RAG chatbot."


@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_omits_summary_for_local_question(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    from sleuth.store import upsert_repo_summary
    upsert_repo_summary(pg_conn, repo_id, "This repo is a small RAG chatbot backend.")
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert not any("This repo is a small RAG chatbot backend." in m["content"] for m in body["messages"])
        sse = 'data: {"choices":[{"delta":{"content":"foo returns 1."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    answer = await get_answer("What does foo do?", repo_id, pg_conn, config)

    assert answer == "foo returns 1."
