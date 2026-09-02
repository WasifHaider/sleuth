import asyncio
import os
import signal

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


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_turn_all_survive_in_transcript():
    """Regression: the spinner used to be drawn as a *transcript line* whose
    text was replaced on a timer, and process_question restarted it after
    every tool call without stopping it first. The still-running spinner
    then overwrote whichever line had been appended most recently, and its
    eventual stop() popped a real content line — so with two or more tool
    calls in a turn, tool output was silently eaten. The spinner now draws
    in place on the terminal line and never touches the transcript.
    """
    events = [
        ToolCallEvent(name="list_files", args={"glob": "*.py"}),
        ToolResultEvent(name="list_files", result="alpha_file.py"),
        ToolCallEvent(name="grep", args={"pattern": "beta_pattern"}),
        ToolResultEvent(name="grep", result="gamma_match.py:7: beta_pattern"),
        ToolCallEvent(name="read_file", args={"path": "delta_file.py"}),
        ToolResultEvent(name="read_file", result="7: epsilon_body"),
        AnswerEvent(text="final answer text"),
    ]

    # Critically, this session yields with a REAL await between events, the
    # way AgentSession does (every event is separated by an awaited HTTP
    # call or subprocess). FakeSession yields everything synchronously, so
    # the spinner's timer task never gets scheduled mid-turn and the bug
    # this test is about is invisible with it.
    class SlowSession:
        def __init__(self, path, config=None):
            self.path = path
            self.questions_received: list[str] = []

        async def ask(self, question):
            self.questions_received.append(question)
            for event in events:
                # Longer than _SPINNER_INTERVAL, so the spinner definitely
                # ticks at least once between consecutive events.
                await asyncio.sleep(0.15)
                yield event

    output = await _drive(
        None,
        "multi tool question\rexit\r",
        session_factory=lambda path, config: SlowSession(path),
    )

    # Every tool call, every tool result, and the answer must all be present.
    for expected in (
        "list_files",
        "alpha_file.py",
        "beta_pattern",
        "gamma_match.py:7",
        "read_file",
        "delta_file.py",
        "epsilon_body",
        "final answer text",
    ):
        assert expected in output, f"{expected!r} was lost from the transcript"
    # And nothing may leak the spinner into the transcript itself.
    assert "thinking" not in output


@pytest.mark.asyncio
async def test_answer_lines_are_not_replaced_by_spinner_text():
    """Regression companion to the test above: a multi-line answer arriving
    after tool activity must keep all of its lines."""

    class SlowSession:
        def __init__(self, path):
            self.path = path

        async def ask(self, question):
            for event in (
                ToolCallEvent(name="grep", args={"pattern": "x"}),
                ToolResultEvent(name="grep", result="a.py:1: x"),
                AnswerEvent(text="line one\nline two\nline three"),
            ):
                await asyncio.sleep(0.15)
                yield event

    output = await _drive(
        None, "q\rexit\r", session_factory=lambda path, config: SlowSession(path)
    )

    assert "line one" in output
    assert "line two" in output
    assert "line three" in output


@pytest.mark.skipif(
    not hasattr(signal, "SIGINT") or os.name == "nt",
    reason="needs POSIX SIGINT delivery to the main thread",
)
@pytest.mark.asyncio
async def test_ctrl_c_interrupts_an_in_flight_turn_instead_of_queueing_behind_it():
    """Regression: Ctrl-C used to be scheduled through the same serialized
    turn queue as questions, so its handler ran only *after* the in-flight
    turn finished — during a slow agentic turn (up to MAX_ITERATIONS
    non-streaming model calls) the REPL was completely unquittable. A turn
    now runs as a cancellable task with SIGINT wired to cancel it.
    """

    class HangingSession:
        def __init__(self, path, config=None):
            self.path = path
            self.started = asyncio.Event()

        async def ask(self, question):
            self.started.set()
            await asyncio.sleep(30)  # far longer than this test may take
            yield AnswerEvent(text="never reached")

    sessions: list[HangingSession] = []

    def factory(path, config):
        session = HangingSession(path)
        sessions.append(session)
        return session

    with create_pipe_input() as pipe_input:
        # A question (which will hang), then Ctrl-D to leave once the
        # interrupted turn has returned control to the prompt.
        pipe_input.send_text("slow question\r")
        repl = asyncio.ensure_future(
            run_repl(
                "/some/path",
                config=None,
                input=pipe_input,
                output=DummyOutput(),
                session_factory=factory,
            )
        )

        # Wait until the turn is genuinely in flight before interrupting.
        for _ in range(200):
            if sessions and sessions[0].started.is_set():
                break
            await asyncio.sleep(0.01)
        assert sessions and sessions[0].started.is_set(), "turn never started"

        os.kill(os.getpid(), signal.SIGINT)

        # The interrupt must land promptly, not after the 30s sleep.
        await asyncio.sleep(0.2)
        pipe_input.send_text("\x04")
        output = await asyncio.wait_for(repl, timeout=5)

    assert "interrupted" in output
    assert "never reached" not in output


@pytest.mark.asyncio
async def test_prompt_has_history_enabled_for_up_arrow_recall():
    """The old REPL bound `up`/`down` globally to transcript scrolling,
    which consumed the input widget's own history recall (and End-of-line).
    Scrolling now belongs to the terminal, so those keys are the prompt's
    again — assert history is actually wired up, since without a History
    instance up-arrow recall silently does nothing.
    """
    import inspect

    from prompt_toolkit.history import History

    from sleuth import repl as repl_module

    source = inspect.getsource(repl_module.run_repl)
    assert "history=" in source
    assert issubclass(repl_module.InMemoryHistory, History)


@pytest.mark.skipif(
    not hasattr(signal, "SIGINT") or os.name == "nt",
    reason="needs POSIX SIGINT delivery to the main thread",
)
@pytest.mark.asyncio
async def test_repl_tells_the_session_its_turn_was_aborted_on_interrupt():
    """Regression: interrupting a turn left the unanswered question in the
    session's message history, so the NEXT question made the model finish
    answering the OLD one (user-reported: interrupt, ask something new, get
    the previous answer). run_turn_interruptibly must notify the session.
    """

    class RecordingSession:
        def __init__(self, path):
            self.path = path
            self.started = asyncio.Event()
            self.aborted = False

        async def ask(self, question):
            self.started.set()
            await asyncio.sleep(30)
            yield AnswerEvent(text="never reached")

        def abort_turn(self):
            self.aborted = True

    sessions: list[RecordingSession] = []

    def factory(path, config):
        s = RecordingSession(path)
        sessions.append(s)
        return s

    with create_pipe_input() as pipe_input:
        pipe_input.send_text("slow question\r")
        repl = asyncio.ensure_future(
            run_repl(
                "/some/path",
                config=None,
                input=pipe_input,
                output=DummyOutput(),
                session_factory=factory,
            )
        )
        for _ in range(200):
            if sessions and sessions[0].started.is_set():
                break
            await asyncio.sleep(0.01)
        assert sessions and sessions[0].started.is_set()

        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(0.2)
        pipe_input.send_text("\x04")
        await asyncio.wait_for(repl, timeout=5)

    assert sessions[0].aborted, "REPL never called session.abort_turn() after interrupt"
