import pytest

from sleuth.retrieve.agentic import run_agentic


class FakeGenerator:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(messages)
        yield self.responses.pop(0)


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
