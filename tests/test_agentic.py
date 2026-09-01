import httpx
import pytest

from sleuth.config import Config
from sleuth.retrieve.agent_session import AGENTIC_GROQ_MODEL, _default_agentic_chain
from sleuth.retrieve.agentic import run_agentic


class FakeGenerator:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(messages)
        yield self.responses.pop(0)


class FailingGenerator:
    """Stands in for a generator whose HTTP call fails every time."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(messages)
        raise self.exc
        yield ""  # pragma: no cover - keeps this an async generator function


@pytest.mark.asyncio
async def test_run_agentic_terminates_immediately_on_non_tool_response(tmp_path):
    fake = FakeGenerator(["Direct answer, no tools needed"])

    result = "".join([t async for t in run_agentic("what is this?", str(tmp_path), config=None, generator=fake)])

    assert result == "Direct answer, no tools needed"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_run_agentic_dispatches_list_files_tool_then_answers(tmp_path):
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n")

    fake = FakeGenerator(
        [
            'TOOL: list_files {"glob": "*.py"}',
            "Found main.py, it defines foo.",
        ]
    )

    result = "".join([t async for t in run_agentic("where is foo?", str(tmp_path), config=None, generator=fake)])

    assert result == "Found main.py, it defines foo."
    assert len(fake.calls) == 2
    tool_result_message = fake.calls[1][-1]["content"]
    assert "main.py" in tool_result_message


@pytest.mark.asyncio
async def test_run_agentic_finds_tool_call_after_leading_reasoning_text(tmp_path):
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n")

    fake = FakeGenerator(
        [
            '<think>\nI should look for foo.\n</think>\nTOOL: list_files {"glob": "*.py"}',
            "Found main.py, it defines foo.",
        ]
    )

    result = "".join([t async for t in run_agentic("where is foo?", str(tmp_path), config=None, generator=fake)])

    assert result == "Found main.py, it defines foo."
    assert len(fake.calls) == 2
    tool_result_message = fake.calls[1][-1]["content"]
    assert "main.py" in tool_result_message


@pytest.mark.asyncio
async def test_run_agentic_enforces_grep_match_cap(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"# match {i}" for i in range(100)))

    fake = FakeGenerator(
        [
            'TOOL: grep {"pattern": "match"}',
            "done",
        ]
    )

    "".join([t async for t in run_agentic("find matches", str(tmp_path), config=None, generator=fake)])

    tool_result_message = fake.calls[1][-1]["content"]
    match_lines = [line for line in tool_result_message.splitlines() if "# match" in line]
    assert len(match_lines) <= 50


@pytest.mark.asyncio
async def test_run_agentic_enforces_read_file_line_cap(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"line {i}" for i in range(1000)))

    fake = FakeGenerator(
        [
            'TOOL: read_file {"path": "big.py"}',
            "done",
        ]
    )

    "".join([t async for t in run_agentic("read the file", str(tmp_path), config=None, generator=fake)])

    tool_result_message = fake.calls[1][-1]["content"]
    assert len(tool_result_message.splitlines()) <= 400


@pytest.mark.asyncio
async def test_run_agentic_hits_iteration_cap_and_notes_cut_short(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    responses = ['TOOL: list_files {"glob": "*.py"}'] * 6 + ["forced final answer"]
    fake = FakeGenerator(responses)

    result = "".join([t async for t in run_agentic("q", str(tmp_path), config=None, generator=fake)])

    assert "forced final answer" in result
    assert "cut short" in result.lower()
    assert len(fake.calls) == 7


@pytest.mark.asyncio
async def test_run_agentic_falls_back_to_next_generator_on_transient_failure(tmp_path):
    failing = FailingGenerator(httpx.HTTPStatusError("boom", request=None, response=None))
    fake = FakeGenerator(["Answer from the fallback generator"])

    result = "".join(
        [
            t
            async for t in run_agentic(
                "what is this?", str(tmp_path), config=None, fallback_chain=[failing, fake]
            )
        ]
    )

    assert result == "Answer from the fallback generator"
    assert len(failing.calls) == 1
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_run_agentic_yields_friendly_message_when_all_generators_fail(tmp_path):
    failing = FailingGenerator(httpx.HTTPStatusError("boom", request=None, response=None))

    result = "".join(
        [
            t
            async for t in run_agentic(
                "what is this?", str(tmp_path), config=None, fallback_chain=[failing]
            )
        ]
    )

    assert "model backend" in result.lower() or "unavailable" in result.lower()
    assert len(failing.calls) == 1


def test_default_agentic_chain_uses_agentic_model_not_config_groq_model():
    config = Config(
        voyage_api_key="vk",
        groq_api_key="gk",
        groq_model="openai/gpt-oss-120b",  # the regular-Q&A model, deliberately different
        database_url="unused",
        nim_api_key=None,
    )

    chain = _default_agentic_chain(config)

    assert len(chain) == 1
    assert chain[0].model_name == AGENTIC_GROQ_MODEL
    assert chain[0].model_name != config.groq_model


def test_default_agentic_chain_includes_nim_fallback_when_key_present():
    config = Config(
        voyage_api_key="vk",
        groq_api_key="gk",
        groq_model="openai/gpt-oss-120b",
        database_url="unused",
        nim_api_key="nk",
    )

    chain = _default_agentic_chain(config)

    assert len(chain) == 2
