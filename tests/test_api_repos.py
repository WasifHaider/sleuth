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
    client.__enter__()  # see tests/test_api_auth.py::_client for why
    client.post(
        "/auth/signup",
        json={"email": "repo-tester@example.com", "password": "correct horse", "name": "Repo Tester"},
    )
    return client


def test_add_repo_requires_session(pg_conn):
    client = TestClient(create_app(_config()))
    client.__enter__()
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
    async def fake_ingest_repo(github_url, conn, config, on_event=None, repo_id=None):
        from sleuth.store import update_repo_status

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


def test_progress_endpoint_returns_404_for_unknown_repo(pg_conn):
    resp = _logged_in_client().get("/repos/00000000-0000-0000-0000-000000000000/progress")
    assert resp.status_code == 404


@respx.mock
def test_progress_endpoint_reports_step(pg_conn, monkeypatch):
    async def fake_ingest_repo(github_url, conn, config, on_event=None, repo_id=None):
        from sleuth.store import update_repo_status

        if on_event:
            on_event("cloned", {"files": 2})
        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id

    monkeypatch.setattr("sleuth.api.routes.repos.ingest_repo", fake_ingest_repo)

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _logged_in_client()

    repo_id = client.post("/repos", json={"github_url": "https://github.com/example/progress-repo"}).json()["id"]

    progress = None
    for _ in range(50):
        progress = client.get(f"/repos/{repo_id}/progress").json()
        if progress["step"] in ("cloned", "ready", "failed"):
            break
        time.sleep(0.1)
    assert progress is not None
    assert "log" in progress
    assert "elapsed_seconds" in progress


@respx.mock
def test_retry_repo_reuses_same_repo_id_no_duplicate_row(pg_conn, monkeypatch):
    call_count = {"n": 0}

    async def fake_ingest_repo(github_url, conn, config, on_event=None, repo_id=None):
        from sleuth.store import update_repo_status

        call_count["n"] += 1
        if call_count["n"] == 1:
            update_repo_status(conn, repo_id, "failed", "boom")
        else:
            update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id

    monkeypatch.setattr("sleuth.api.routes.repos.ingest_repo", fake_ingest_repo)
    client = _logged_in_client()

    repo_id = client.post("/repos", json={"github_url": "https://github.com/example/retry-repo"}).json()["id"]

    for _ in range(50):
        if client.get(f"/repos/{repo_id}").json()["status"] == "failed":
            break
        time.sleep(0.1)
    assert client.get(f"/repos/{repo_id}").json()["status"] == "failed"

    resp = client.post(f"/repos/{repo_id}/retry")
    assert resp.status_code == 200
    assert resp.json()["id"] == repo_id  # same row, not a new one

    for _ in range(50):
        if client.get(f"/repos/{repo_id}").json()["status"] == "ready":
            break
        time.sleep(0.1)

    all_repos = client.get("/repos").json()
    assert len([r for r in all_repos if r["github_url"] == "https://github.com/example/retry-repo"]) == 1


def test_retry_repo_returns_404_for_unknown_repo(pg_conn):
    resp = _logged_in_client().post("/repos/00000000-0000-0000-0000-000000000000/retry")
    assert resp.status_code == 404


def test_delete_repo_removes_it_from_list(pg_conn, monkeypatch):
    async def fake_ingest_repo(github_url, conn, config, on_event=None, repo_id=None):
        from sleuth.store import update_repo_status

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id

    monkeypatch.setattr("sleuth.api.routes.repos.ingest_repo", fake_ingest_repo)
    client = _logged_in_client()
    repo_id = client.post("/repos", json={"github_url": "https://github.com/example/to-delete"}).json()["id"]

    resp = client.delete(f"/repos/{repo_id}")
    assert resp.status_code == 200
    assert resp.json() == {"id": repo_id, "deleted": True}

    assert client.get(f"/repos/{repo_id}").status_code == 404
    all_repos = client.get("/repos").json()
    assert all(r["id"] != repo_id for r in all_repos)


def test_delete_repo_returns_404_for_unknown_repo(pg_conn):
    resp = _logged_in_client().delete("/repos/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
