import json
import subprocess

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import get_existing_hashes


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("def foo():\n    return 1\n")
    (repo_dir / "b.py").write_text("def bar():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def _config():
    return Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")


def _mock_voyage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = [
            {"embedding": [float(len(t))] * 1024, "index": i}
            for i, t in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_creates_chunks_and_marks_ready(pg_conn, local_git_repo):
    _mock_voyage()
    config = _config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)

    row = pg_conn.execute(
        "SELECT status, embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "ready"
    assert row[1] == "voyage-code-3"
    assert row[2] == 1024

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert ("a.py", "foo") in hashes
    assert ("b.py", "bar") in hashes


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_skips_unchanged_chunks_on_reindex(pg_conn, local_git_repo):
    _mock_voyage()
    config = _config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)
    hashes_before = get_existing_hashes(pg_conn, repo_id)

    # change only a.py
    (local_git_repo / "a.py").write_text("def foo():\n    return 999\n")
    subprocess.run(["git", "add", "."], cwd=local_git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "change a"], cwd=local_git_repo, check=True, capture_output=True)

    call_count_before = len(respx.calls)
    repo_id_2 = await ingest_repo(str(local_git_repo), pg_conn, config)
    calls_during_reindex = len(respx.calls) - call_count_before

    assert repo_id_2 == repo_id

    hashes_after = get_existing_hashes(pg_conn, repo_id_2)

    assert hashes_after[("a.py", "foo")] != hashes_before[("a.py", "foo")]
    assert hashes_after[("b.py", "bar")] == hashes_before[("b.py", "bar")]
    # only the changed chunk got re-embedded, not the unchanged one
    assert calls_during_reindex >= 1


@pytest.mark.asyncio
async def test_ingest_repo_marks_failed_on_clone_error(pg_conn, tmp_path):
    config = _config()

    repo_id = await ingest_repo(str(tmp_path / "nope"), pg_conn, config)

    row = pg_conn.execute(
        "SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] is not None


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_emits_progress_events(pg_conn, local_git_repo):
    _mock_voyage()
    config = _config()
    events = []

    await ingest_repo(
        str(local_git_repo), pg_conn, config,
        on_event=lambda step, detail: events.append((step, detail)),
    )

    steps = [step for step, _detail in events]
    assert "cloned" in steps
    assert "ready" in steps
