import json
import subprocess

import httpx
import pytest
import respx

from sleuth.cli import main
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("def foo():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


@pytest.mark.asyncio
@respx.mock
async def test_cli_add_list_ask_end_to_end(pg_conn, local_git_repo, monkeypatch, capsys):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = [{"embedding": [0.1] * 1024, "index": i} for i, _ in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        sse = 'data: {"choices":[{"delta":{"content":"foo returns 1."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    await main(["add", str(local_git_repo)])
    out = capsys.readouterr().out
    assert "ready" in out

    await main(["list"])
    out = capsys.readouterr().out
    assert str(local_git_repo) in out

    row = pg_conn.execute("SELECT id FROM repos WHERE github_url = %s", (str(local_git_repo),)).fetchone()
    pg_conn.commit()
    repo_id = str(row[0])

    await main(["ask", repo_id, "What does foo do?"])
    out = capsys.readouterr().out
    assert "foo returns 1." in out


@pytest.mark.asyncio
async def test_cli_agentic_smoke(monkeypatch, capsys, pg_conn, tmp_path):
    async def fake_run_agentic(question, path, config):
        yield "stub agentic answer"

    monkeypatch.setattr("sleuth.cli.run_agentic", fake_run_agentic)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    await main(["agentic", str(tmp_path), "what does this do?"])
    out = capsys.readouterr().out
    assert "stub agentic answer" in out


@pytest.mark.asyncio
async def test_cli_eval_smoke(monkeypatch, capsys, pg_conn, tmp_path):
    async def fake_run_eval(golden_yaml_path, conn, config):
        return "stub eval table"

    monkeypatch.setattr("sleuth.cli.run_eval", fake_run_eval)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text("repo: x\ncases: []\n")

    await main(["eval", str(golden_path)])
    out = capsys.readouterr().out
    assert "stub eval table" in out
