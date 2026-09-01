import httpx
import pytest

from sleuth.retrieve.agent_session import AgentSession, AnswerEvent, ToolCallEvent, ToolResultEvent


class FakeGenerator:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(list(messages))  # snapshot: AgentSession mutates
        # the same list object across iterations, so storing a reference
        # here would make every past call() look like it received the
        # FINAL message state instead of what it actually saw at call time.
        yield self.responses.pop(0)


class FailingGenerator:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(messages)
        raise self.exc
        yield ""  # pragma: no cover - keeps this an async generator function


@pytest.mark.asyncio
async def test_agent_session_multi_turn_retains_context(tmp_path):
    fake = FakeGenerator(
        [
            "First answer",
            "Second answer, building on the first",
        ]
    )
    session = AgentSession(str(tmp_path), config=None, generator=fake)

    first = [e async for e in session.ask("What is this repo?")]
    assert isinstance(first[-1], AnswerEvent)
    assert first[-1].text == "First answer"

    second = [e async for e in session.ask("And what about the tests?")]
    assert second[-1].text == "Second answer, building on the first"

    # The second LLM call's message list must carry the whole prior
    # exchange, not just the new question — this is what makes it a real
    # multi-turn session rather than two independent one-shot calls.
    second_call_messages = fake.calls[-1]
    contents = [m["content"] for m in second_call_messages]
    assert "What is this repo?" in contents
    assert "First answer" in contents
    assert "And what about the tests?" in contents


@pytest.mark.asyncio
async def test_agent_session_yields_tool_call_and_result_events_then_answer(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    fake = FakeGenerator(
        [
            'TOOL: list_files {"glob": "*.py"}',
            "Found it.",
        ]
    )
    session = AgentSession(str(tmp_path), config=None, generator=fake)

    events = [e async for e in session.ask("where is it?")]

    assert isinstance(events[0], ToolCallEvent)
    assert events[0].name == "list_files"
    assert events[0].args == {"glob": "*.py"}

    assert isinstance(events[1], ToolResultEvent)
    assert events[1].name == "list_files"
    assert "main.py" in events[1].result

    assert isinstance(events[2], AnswerEvent)
    assert events[2].text == "Found it."


@pytest.mark.asyncio
async def test_agent_session_trims_oldest_tool_call_result_pair_when_over_cap(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    # Enough tool-call iterations to exceed a small cap, then verify the
    # system prompt survives and the message list shrinks back under the
    # cap rather than growing forever across a long session.
    responses = ['TOOL: list_files {"glob": "*.py"}'] * 4 + ["done"]
    fake = FakeGenerator(responses)
    session = AgentSession(str(tmp_path), config=None, generator=fake, max_messages=5)

    await_events = [e async for e in session.ask("investigate")]
    assert await_events  # sanity: the session actually produced events

    assert len(session.messages) <= 5
    assert session.messages[0]["role"] == "system"


@pytest.mark.asyncio
async def test_agent_session_yields_unavailable_message_when_all_generators_fail(tmp_path):
    failing = FailingGenerator(httpx.HTTPStatusError("boom", request=None, response=None))
    session = AgentSession(str(tmp_path), config=None, fallback_chain=[failing])

    events = [e async for e in session.ask("q")]

    assert isinstance(events[-1], AnswerEvent)
    assert "unavailable" in events[-1].text.lower()


@pytest.mark.asyncio
async def test_agent_session_hits_iteration_cap_and_marks_answer_truncated(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    responses = ['TOOL: list_files {"glob": "*.py"}'] * 6 + ["forced final answer"]
    fake = FakeGenerator(responses)
    session = AgentSession(str(tmp_path), config=None, generator=fake)

    events = [e async for e in session.ask("q")]

    assert isinstance(events[-1], AnswerEvent)
    assert "forced final answer" in events[-1].text
    assert events[-1].truncated is True
    assert len(fake.calls) == 7


@pytest.mark.asyncio
async def test_agent_session_retries_on_malformed_tool_call_json_instead_of_misfiring(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    fake = FakeGenerator(
        [
            # Malformed: unquoted key make this fail json.loads, even
            # though it clearly LOOKS like an attempted tool call, not a
            # real prose answer.
            'TOOL: grep {pattern: "def foo"}',
            'TOOL: grep {"pattern": "def foo"}',
            "Found it: a.py defines foo.",
        ]
    )
    session = AgentSession(str(tmp_path), config=None, generator=fake)

    events = [e async for e in session.ask("where is foo?")]

    # The malformed line must never be shown to the user as if it were the
    # final answer.
    assert not any(isinstance(e, AnswerEvent) and "TOOL:" in e.text for e in events)
    assert isinstance(events[-1], AnswerEvent)
    assert events[-1].text == "Found it: a.py defines foo."
    # 3 real LLM calls: malformed attempt, corrected retry, final answer.
    assert len(fake.calls) == 3
    # The malformed attempt's error must have been fed back as a tool
    # result so the model actually sees what went wrong.
    retry_prompt_messages = fake.calls[1]
    last_user_message = retry_prompt_messages[-1]["content"]
    assert "malformed" in last_user_message.lower() or "invalid" in last_user_message.lower()


# --- real-tool subprocess-backed grep/list_files/read_file (Phase 0, items 4-6) ---


def test_tool_grep_finds_matches_via_ripgrep(tmp_path):
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")

    result = _tool_grep(tmp_path, "def foo")

    assert "a.py" in result
    assert "def foo" in result
    assert "b.py" not in result


def test_tool_grep_no_matches_returns_placeholder(tmp_path):
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / "a.py").write_text("x = 1\n")

    result = _tool_grep(tmp_path, "nonexistent_pattern_xyz")

    assert result == "(no matches)"


def test_tool_grep_invalid_regex_returns_error_string_not_raise(tmp_path):
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / "a.py").write_text("x = 1\n")

    result = _tool_grep(tmp_path, "(unclosed[")

    assert "invalid" in result.lower() or "error" in result.lower()


def test_tool_grep_respects_gitignore(tmp_path):
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / ".gitignore").write_text("ignored_dir/\n")
    ignored = tmp_path / "ignored_dir"
    ignored.mkdir()
    (ignored / "secret.py").write_text("def foo(): pass\n")
    (tmp_path / "real.py").write_text("def foo(): pass\n")

    result = _tool_grep(tmp_path, "def foo")

    assert "real.py" in result
    assert "secret.py" not in result


def test_tool_grep_caps_at_50_matches(tmp_path):
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / "big.py").write_text("\n".join(f"# match {i}" for i in range(100)))

    result = _tool_grep(tmp_path, "match")

    match_lines = [line for line in result.splitlines() if "match" in line]
    assert len(match_lines) <= 51  # 50 matches + possible truncation note
    assert "truncated" in result.lower()


def test_tool_list_files_via_ripgrep(tmp_path):
    from sleuth.retrieve.agent_session import _tool_list_files

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.txt").write_text("hello\n")

    result = _tool_list_files(tmp_path, "*.py")

    assert "a.py" in result
    assert "b.txt" not in result


def test_tool_list_files_caps_at_200(tmp_path):
    from sleuth.retrieve.agent_session import _tool_list_files

    for i in range(250):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")

    result = _tool_list_files(tmp_path, "*.py")

    file_lines = [line for line in result.splitlines() if line.endswith(".py")]
    assert len(file_lines) <= 200
    assert "truncated" in result.lower()


def test_tool_read_file_returns_numbered_lines(tmp_path):
    from sleuth.retrieve.agent_session import _tool_read_file

    (tmp_path / "a.py").write_text("line one\nline two\nline three\n")

    result = _tool_read_file(tmp_path, "a.py")

    assert "1: line one" in result
    assert "2: line two" in result
    assert "3: line three" in result


def test_tool_read_file_rejects_path_traversal(tmp_path):
    from sleuth.retrieve.agent_session import _tool_read_file

    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("top secret\n")
    try:
        result = _tool_read_file(tmp_path, "../outside_secret.txt")
        assert "top secret" not in result
        assert "error" in result.lower() or "invalid" in result.lower()
    finally:
        outside.unlink(missing_ok=True)


def test_tool_read_file_caps_at_400_lines(tmp_path):
    from sleuth.retrieve.agent_session import _tool_read_file

    (tmp_path / "big.py").write_text("\n".join(f"line {i}" for i in range(1000)))

    result = _tool_read_file(tmp_path, "big.py")

    assert len(result.splitlines()) <= 400
