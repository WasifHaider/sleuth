import time

import httpx
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
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
        json={"email": "repo-tester@example.com", "password": "correct horse", "name": "Repo Tester"},
    )
    return client


def test_add_repo_requires_session(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.post("/repos", json={"github_url": "https://github.com/example/repo"})
    assert resp.status_code == 401


def test_get_unknown_repo_returns_404(pg_conn):
    resp = _logged_in_client().get("/repos/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@respx.mock
def test_add_list_get_repo_round_trip(pg_conn, monkeypatch):
    # This test only exercises the routing/wiring (POST creates a pending repo,
    # background task drives it to a terminal state) — real cloning/embedding
    # behavior is already covered by tests/test_pipeline.py. Stub ingest_repo
    # so this doesn't shell out to a real `git clone` against a fake GitHub URL,
    # which just hangs waiting on network/credentials.
    async def fake_ingest_repo(github_url, conn, config):
        from sleuth.store import update_repo_status

        rows = conn.execute("SELECT id FROM repos WHERE github_url = %s", (github_url,)).fetchall()
        repo_id = str(rows[-1][0])
        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id

    monkeypatch.setattr("sleuth.api.routes.repos.ingest_repo", fake_ingest_repo)

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _logged_in_client()

    resp = client.post("/repos", json={"github_url": "https://github.com/example/repo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["github_url"] == "https://github.com/example/repo"
    assert body["status"] == "pending"
    repo_id = body["id"]

    listed = client.get("/repos").json()
    assert any(r["id"] == repo_id for r in listed)

    for _ in range(50):
        got = client.get(f"/repos/{repo_id}").json()
        if got["status"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert got["status"] in ("ready", "failed")
