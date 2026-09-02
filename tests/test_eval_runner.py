import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.eval.runner import load_golden, run_eval
from sleuth.store import create_repo, update_repo_status, upsert_chunks

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_golden.yaml"


def test_load_golden_parses_cases():
    repo, cases = load_golden(str(FIXTURE_PATH))

    assert repo == "example-repo"
    assert len(cases) == 1
    assert cases[0].question == "Where is foo defined?"
    assert cases[0].expected_files == ["f.py"]
    assert cases[0].expected_symbols == ["foo"]


@pytest.mark.asyncio
@respx.mock
async def test_run_eval_computes_hit_rate_mrr_and_judge_score(pg_conn, tmp_path):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(
        yaml.dump(
            {
                "repo": repo_id,
                "cases": [
                    {
                        "question": "Where is foo defined?",
                        "expected_files": ["f.py"],
                        "expected_symbols": ["foo"],
                        "reference_answer": "foo is defined in f.py and returns 1.",
                    }
                ],
            }
        )
    )

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        if "Score how well" in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "5"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "foo is defined in f.py."}}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(
        voyage_api_key="k",
        groq_api_key="k",
        groq_model="test-model",
        database_url="unused",
    )

    table = await run_eval(str(golden_path), pg_conn, config)

    assert "hit-rate@8: 1.00" in table
    assert "MRR: 1.00" in table
    assert "avg judge: 5.0" in table


@pytest.mark.asyncio
@respx.mock
async def test_run_eval_paces_calls_between_cases(pg_conn, tmp_path, monkeypatch):
    # A golden set with several cases fires 2 Groq calls per case (answer +
    # judge) back-to-back with no delay — confirmed live against a real
    # 9-case golden set that this reliably blows through Groq's per-minute
    # token budget mid-run. run_eval must sleep between cases so a larger
    # golden set doesn't hammer the provider in a tight loop.
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(
        yaml.dump(
            {
                "repo": repo_id,
                "cases": [
                    {"question": "Q1", "expected_files": ["f.py"]},
                    {"question": "Q2", "expected_files": ["f.py"]},
                ],
            }
        )
    )

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "an answer"}}]})
    )

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("sleuth.eval.runner.asyncio.sleep", fake_sleep)

    config = Config(
        voyage_api_key="k",
        groq_api_key="k",
        groq_model="test-model",
        database_url="unused",
    )

    await run_eval(str(golden_path), pg_conn, config)

    # VoyageEmbedder's own internal per-minute pacing (FREE_TIER_REQUESTS_
    # PER_MINUTE, see sleuth/ingest/embed.py) also calls the real asyncio.
    # sleep and gets caught by this same monkeypatch (asyncio is one shared
    # module object) — that's pre-existing, unrelated behavior. What this
    # test actually verifies is that run_eval's own INTER_CASE_DELAY_SECONDS
    # pause fired exactly once, between the 2 cases (not before the first,
    # not twice).
    from sleuth.eval.runner import INTER_CASE_DELAY_SECONDS

    assert sleeps.count(INTER_CASE_DELAY_SECONDS) == 1
