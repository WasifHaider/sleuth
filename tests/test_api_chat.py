import httpx
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.store import create_repo, update_repo_status, upsert_chunks
from tests.conftest import TEST_DATABASE_URL


def _config():
    return Config(
        voyage_api_key="k", groq_api_key="k", groq_model="m",
        database_url=TEST_DATABASE_URL,
        session_secret="test-secret-not-for-prod",
    )


def _logged_in_client() -> TestClient:
    client = TestClient(create_app(_config()))
    client.post(
        "/auth/signup",
        json={"email": "chat-tester@example.com", "password": "correct horse", "name": "Chat Tester"},
    )
    return client


def test_create_chat_requires_session(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()
    client = TestClient(create_app(_config()))
    resp = client.post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 401


def test_create_chat_requires_ready_repo(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()  # status defaults to pending
    resp = _logged_in_client().post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 409


def test_create_list_chat_and_messages_round_trip(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()

    client = _logged_in_client()
    created = client.post("/chats", json={"repo_id": repo_id}).json()
    assert created["title"] == "New chat"

    listed = client.get(f"/chats?repo_id={repo_id}").json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["message_count"] == 0

    messages = client.get(f"/chats/{created['id']}/messages").json()
    assert messages == []


def test_get_messages_for_unknown_chat_returns_404(pg_conn):
    resp = _logged_in_client().get("/chats/00000000-0000-0000-0000-000000000000/messages")
    assert resp.status_code == 404


def test_post_chat_requires_session(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()
    client = TestClient(create_app(_config()))
    resp = client.post("/chat", json={"chat_id": "irrelevant", "question": "q?"})
    assert resp.status_code == 401


@respx.mock
def test_post_chat_streams_tokens_and_persists_messages(pg_conn):
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

    client = _logged_in_client()
    chat_id = client.post("/chats", json={"repo_id": repo_id}).json()["id"]

    with client.stream("POST", "/chat", json={"chat_id": chat_id, "question": "what does foo do?"}) as resp:
        body = "".join(resp.iter_text())

    assert "event: sources" in body
    assert "event: done" in body

    messages = client.get(f"/chats/{chat_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hi"
    assert messages[1]["sources"][0]["file_path"] == "f.py"


def test_post_chat_rejects_unknown_chat(pg_conn):
    resp = _logged_in_client().post("/chat", json={"chat_id": "00000000-0000-0000-0000-000000000000", "question": "q?"})
    assert resp.status_code == 404
