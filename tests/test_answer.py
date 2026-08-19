import json

import httpx
import pytest
import respx

from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.retrieve.answer import build_prompt, get_answer
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
