import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from sleuth.repl import EXIT_COMMANDS, run_repl
from sleuth.retrieve.agent_session import AnswerEvent, ToolCallEvent, ToolResultEvent


class FakeSession:
    """Stands in for AgentSession: ask() returns a pre-scripted sequence of
    event-lists, one list consumed per call, so a test can script exactly
    what happens on turn 1, turn 2, etc."""

    def __init__(self, path, scripted_turns):
        self.path = path
        self._turns = list(scripted_turns)
        self.questions_received: list[str] = []

    async def ask(self, question: str):
        self.questions_received.append(question)
        events = self._turns.pop(0)
        for event in events:
            yield event


def _make_session_factory(scripted_turns):
    def factory(path, config):
        return FakeSession(path, scripted_turns)

    return factory


async def _drive(scripted_turns, keystrokes: str, session_factory=None, sessions_created=None):
    """Runs run_repl() against a real prompt_toolkit Application driven by
    a pipe Input, feeding `keystrokes` (prompt_toolkit key notation, '\\r'
    for Enter) and returning the final transcript text once the app exits
    (keystrokes must end in a way that triggers exit, e.g. 'exit\\r').
    """
    if session_factory is None:
        if sessions_created is not None:

            def factory(path, config):
                session = FakeSession(path, scripted_turns)
                sessions_created.append(session)
                return session

            session_factory = factory
        else:
            session_factory = _make_session_factory(scripted_turns)

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keystrokes)
        return await run_repl(
            "/some/path",
            config=None,
            input=pipe_input,
            output=DummyOutput(),
            session_factory=session_factory,
        )


@pytest.mark.asyncio
async def test_repl_answers_one_question_then_exits_on_quit():
    scripted = [[AnswerEvent(text="This repo is a RAG chatbot.")]]

    output = await _drive(scripted, "what is this repo?\rexit\r")

    assert "This repo is a RAG chatbot." in output


@pytest.mark.asyncio
async def test_repl_multi_turn_uses_same_session_for_both_questions():
    scripted = [
        [AnswerEvent(text="First answer")],
        [AnswerEvent(text="Second answer")],
    ]
    sessions_created = []

    output = await _drive(
        scripted, "first question\rsecond question\rquit\r", sessions_created=sessions_created
    )

    assert len(sessions_created) == 1
    assert sessions_created[0].questions_received == ["first question", "second question"]
    assert "First answer" in output
    assert "Second answer" in output


@pytest.mark.asyncio
async def test_repl_prints_tool_call_and_result_distinctly_from_answer():
    scripted = [
        [
            ToolCallEvent(name="grep", args={"pattern": "def foo"}),
            ToolResultEvent(name="grep", result="a.py:1: def foo():"),
            AnswerEvent(text="foo is defined in a.py"),
        ]
    ]

    output = await _drive(scripted, "where is foo?\rexit\r")

    assert "grep" in output
    assert "def foo" in output  # the tool args were shown
    assert "a.py:1" in output  # the tool result was shown
    assert "foo is defined in a.py" in output
    # Tool activity and the final answer must be visually distinguishable —
    # check the tool line is marked differently from a plain answer line.
    tool_call_line = next(line for line in output.splitlines() if "grep" in line)
    answer_line = next(line for line in output.splitlines() if "foo is defined in a.py" in line)
    assert tool_call_line != answer_line


@pytest.mark.asyncio
async def test_repl_ignores_blank_lines_without_calling_ask():
    scripted = [[AnswerEvent(text="answer")]]
    sessions_created = []

    await _drive(scripted, "\r\rreal question\rexit\r", sessions_created=sessions_created)

    assert sessions_created[0].questions_received == ["real question"]


@pytest.mark.asyncio
async def test_repl_exits_cleanly_on_ctrl_d_with_no_exit_command():
    scripted = [[AnswerEvent(text="answer")]]

    output = await _drive(scripted, "one question\r\x04")

    assert "answer" in output


def test_exit_commands_include_common_variants():
    assert "exit" in EXIT_COMMANDS
    assert "quit" in EXIT_COMMANDS
