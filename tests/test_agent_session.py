import asyncio

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


def test_tool_read_file_caps_at_max_chars_even_under_400_lines(tmp_path):
    """Regression: the line cap alone assumes short/code-like lines. A file
    with FEW lines but long prose (this project's real CLAUDE.md: 217
    lines, 32,235 bytes) passed the 400-line cap cleanly while still
    dumping a huge tool result back into the conversation — confirmed live,
    this caused a ReadTimeout on the NEXT LLM call during real repo
    summarization (both the primary and NIM fallback generator), because
    only a line count was ever bounded, never a character count."""
    from sleuth.retrieve.agent_session import READ_FILE_MAX_CHARS, _tool_read_file

    # 50 lines, each individually huge — well under 400 lines, but the
    # total text should still exceed READ_FILE_MAX_CHARS.
    long_line = "x" * 500
    (tmp_path / "prose.md").write_text("\n".join(long_line for _ in range(50)))

    result = _tool_read_file(tmp_path, "prose.md")

    assert len(result) <= READ_FILE_MAX_CHARS + 200  # + truncation note's own length
    assert "truncated" in result.lower()


def test_tool_read_file_does_not_truncate_when_under_char_cap(tmp_path):
    from sleuth.retrieve.agent_session import _tool_read_file

    (tmp_path / "small.py").write_text("x = 1\ny = 2\n")

    result = _tool_read_file(tmp_path, "small.py")

    assert "truncated" not in result.lower()


# --- Regression tests: the three Windows/ripgrep tool-layer bugs ---------
# All three were reproduced end-to-end on a real repo before fixing:
# (1) locale (cp1252) decoding of UTF-8 tool output crashing on a
#     background reader thread, (2) an explicit match-everything --glob
#     switching ripgrep out of gitignore-respecting mode, and (3) rg's
#     exit code 2 (unreadable symlink) discarding real matches and telling
#     the model its pattern was invalid.


def test_tool_grep_decodes_utf8_output_instead_of_locale_codepage(tmp_path):
    """Regression (bug 1): _tool_grep used subprocess text=True with no
    encoding=, so output was decoded with the locale codepage — cp1252 on
    native Windows Python. A curly quote (U+201C, bytes \xe2\x80\x9c) in a
    matched line then raised UnicodeDecodeError inside subprocess's
    background reader thread, printing a bare traceback while
    subprocess.run() still "succeeded" with the output lost. Decoding is
    now pinned to UTF-8.
    """
    from sleuth.retrieve.agent_session import _tool_grep

    # 0x9d is precisely the byte that has no cp1252 mapping.
    (tmp_path / "quotes.py").write_text(
        'label = \u201cwell known\u201d  # curly quotes\n', encoding="utf-8"
    )

    result = _tool_grep(tmp_path, "well known")

    assert "quotes.py" in result
    assert "well known" in result
    assert "codec" not in result.lower()


def test_tool_read_file_decodes_utf8_content(tmp_path):
    """Same bug 1, in the tool most likely to hit it: read_file reads whole
    source files, where non-ASCII text is common."""
    from sleuth.retrieve.agent_session import _tool_read_file

    (tmp_path / "u.py").write_text(
        '# na\u00efve \u201cquoted\u201d caf\u00e9 \u2014 em dash\nx = 1\n', encoding="utf-8"
    )

    result = _tool_read_file(tmp_path, "u.py")

    assert "quoted" in result
    assert "caf\u00e9" in result


def test_tool_grep_star_glob_does_not_defeat_gitignore(tmp_path):
    """Regression (bug 2): passing any explicit --glob makes ripgrep stop
    honoring .gitignore for matching, so glob="*" (a no-op as a filter, and
    something models add reflexively) dragged every ignored directory back
    into the search. Measured on the real repo: 12,853 .venv files scanned
    and a 420x output blowup. A match-everything glob is now dropped.
    """
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / ".gitignore").write_text("ignored_dir/\n")
    ignored = tmp_path / "ignored_dir"
    ignored.mkdir()
    (ignored / "vendored.py").write_text("def target(): pass\n")
    (tmp_path / "real.py").write_text("def target(): pass\n")

    result = _tool_grep(tmp_path, "def target", glob="*")

    assert "real.py" in result
    assert "vendored.py" not in result, "'*' glob re-enabled ignored dirs"


def test_tool_list_files_star_glob_does_not_defeat_gitignore(tmp_path):
    """Same bug 2 in list_files, where glob="*" is the single most likely
    call a model makes to survey a project."""
    from sleuth.retrieve.agent_session import _tool_list_files

    (tmp_path / ".gitignore").write_text("ignored_dir/\n")
    ignored = tmp_path / "ignored_dir"
    ignored.mkdir()
    (ignored / "vendored.py").write_text("x = 1\n")
    (tmp_path / "real.py").write_text("x = 1\n")

    result = _tool_list_files(tmp_path, "*")

    assert "real.py" in result
    assert "vendored.py" not in result


def test_tool_grep_real_glob_still_filters(tmp_path):
    """Guard on the bug-2 fix: only match-everything globs are dropped. A
    genuine glob must still filter, or the fix would have silently broken
    every targeted search."""
    from sleuth.retrieve.agent_session import _tool_grep

    (tmp_path / "a.py").write_text("needle here\n")
    (tmp_path / "b.txt").write_text("needle here\n")

    result = _tool_grep(tmp_path, "needle", glob="*.py")

    assert "a.py" in result
    assert "b.txt" not in result


def test_tool_grep_returns_matches_when_rg_also_reports_unreadable_paths(tmp_path, monkeypatch):
    """Regression (bug 3): rg exits 2 if ANY path was unreadable — routine
    here, since .venv/lib64 is a WSL symlink Windows rg can't traverse
    ("os error 1920"), as are npm's node_modules/.bin/* links. _tool_grep
    treated every exit 2 as "invalid pattern", discarding the ~195KB of
    real matches rg had printed to stdout. The model was told its search
    failed when it hadn't, so it retried until MAX_ITERATIONS was gone —
    the REPL's "gets stuck on a codebase question" symptom.
    """
    import subprocess as real_subprocess

    from sleuth.retrieve import agent_session

    def fake_run(args, **kwargs):
        return real_subprocess.CompletedProcess(
            args=args,
            returncode=2,  # partial failure
            stdout=f"{tmp_path}/real.py:1: def target(): pass\n",
            stderr="rg: ./.venv/lib64: The file cannot be accessed by the system. (os error 1920)\n",
        )

    monkeypatch.setattr(agent_session, "_run_tool_subprocess", fake_run)

    result = agent_session._tool_grep(tmp_path, "def target")

    assert "real.py" in result, "real matches were discarded on exit code 2"
    assert "invalid pattern" not in result.lower()
    assert "skipped" in result.lower()  # the warning is surfaced, not hidden


def test_tool_grep_still_reports_error_when_exit_2_yields_no_matches(tmp_path, monkeypatch):
    """Guard on the bug-3 fix: a genuinely broken pattern (exit 2, empty
    stdout) must STILL be reported as an error, so the model can correct
    its own regex. Blanket-accepting exit 2 would have broken that."""
    import subprocess as real_subprocess

    from sleuth.retrieve import agent_session

    def fake_run(args, **kwargs):
        return real_subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout="",
            stderr="rg: regex parse error: unclosed character class\n",
        )

    monkeypatch.setattr(agent_session, "_run_tool_subprocess", fake_run)

    result = agent_session._tool_grep(tmp_path, "(unclosed[")

    assert "error" in result.lower() or "invalid" in result.lower()


def test_relativize_rg_line_handles_windows_drive_letter_paths():
    """Regression (bug 4, found while verifying the others): the parser used
    line.split(":", 2), but a Windows absolute path carries its own
    drive-letter colon. "C:\repo\a.py:1:x" split into 3 parts gives
    path="C", lineno="\repo\a.py", content="1:x" — so relative_to() always
    failed, paths stayed absolute (leaking the host's filesystem layout
    into the model's context) and the "path:lineno: text" contract the
    model cites from was mangled. Now anchored on the ":<digits>:"
    separator instead.
    """
    from pathlib import PureWindowsPath

    from sleuth.retrieve.agent_session import _RG_LINE_RE

    line = r"C:\repo\pkg\a.py:42:    def target(self):"
    match = _RG_LINE_RE.match(line)

    assert match is not None
    assert match.group("path") == r"C:\repo\pkg\a.py"
    assert match.group("lineno") == "42"
    assert match.group("content") == "    def target(self):"
    # And the path is genuinely relativizable against its root.
    root = PureWindowsPath(r"C:\repo")
    assert str(PureWindowsPath(match.group("path")).relative_to(root)) == r"pkg\a.py"


def test_relativize_rg_line_keeps_colons_in_matched_content(tmp_path):
    """Companion to the bug-4 fix: the non-greedy path match must not eat
    into content that itself contains colons (dict literals, type hints,
    URLs) — a greedy pattern would split on the LAST ':<digits>:'."""
    from sleuth.retrieve.agent_session import _relativize_rg_line

    line = f"{tmp_path}/a.py:7:url = \"http://x:8080/p\"  # port:8080"

    result = _relativize_rg_line(line, tmp_path)

    assert result.startswith("a.py:7:")
    assert "http://x:8080/p" in result
    assert "port:8080" in result


@pytest.mark.asyncio
async def test_interrupted_turn_does_not_leak_into_the_next_question(tmp_path):
    """Regression: ask() mutates self.messages as it runs, so cancelling a
    turn mid-flight left the ORIGINAL question sitting in history
    unanswered, with all of its tool results still attached. The next
    question was simply appended after it, so the model saw a still-pending
    investigation plus a short new message and finished answering the OLD
    question — the user typed a fresh prompt and got the previous
    question's answer back.

    Only reachable since the REPL gained real Ctrl-C interruption: before
    that, a turn always ran to completion, so ask() could never be
    abandoned part-way.
    """

    class HangingThenAnsweringGenerator:
        """Call 1 returns a tool call, call 2 hangs (so the test can cancel
        while the model is 'thinking'), call 3 answers normally."""

        def __init__(self):
            self.calls: list[list[dict]] = []
            self.n = 0

        async def chat(self, messages: list[dict], stream: bool = True):
            self.calls.append([dict(m) for m in messages])
            self.n += 1
            if self.n == 1:
                yield 'TOOL: grep {"pattern": "def target"}'
            elif self.n == 2:
                await asyncio.sleep(3600)  # cancelled here
                yield ""
            else:
                yield "4"

    (tmp_path / "a.py").write_text("def target(): pass\n")
    gen = HangingThenAnsweringGenerator()
    session = AgentSession(str(tmp_path), generator=gen)

    async def run_first_turn():
        async for _ in session.ask("how is authentication handled?"):
            pass

    task = asyncio.ensure_future(run_first_turn())
    for _ in range(500):  # wait until the 2nd (hanging) call is in flight
        if gen.n >= 2:
            break
        await asyncio.sleep(0.01)
    assert gen.n >= 2, "second model call never started"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # This is what the REPL does on Ctrl-C.
    session.abort_turn()

    # Now a completely unrelated follow-up question.
    [_ async for _ in session.ask("what is 2 + 2?")]

    messages_seen = gen.calls[-1]
    joined = "\n".join(m["content"] for m in messages_seen)

    # The abandoned question must be explicitly marked as abandoned, or the
    # model will just carry on answering it.
    assert "interrupt" in joined.lower(), (
        "no interruption marker — the model still sees the old question as pending:\n"
        + joined[-800:]
    )
    # And the new question must be the most recent thing the model sees.
    assert "2 + 2" in messages_seen[-1]["content"]


@pytest.mark.asyncio
async def test_abort_turn_keeps_message_roles_well_formed(tmp_path):
    """abort_turn() must not leave two consecutive same-role messages or a
    dangling assistant tool-call with no result — _trim_messages() drops
    messages in assistant/user PAIRS (del self.messages[1:3]), so broken
    pairing there silently orphans a tool result against the wrong call.
    """

    class ToolThenHang:
        def __init__(self):
            self.n = 0

        async def chat(self, messages: list[dict], stream: bool = True):
            self.n += 1
            if self.n == 1:
                yield 'TOOL: grep {"pattern": "def target"}'
            else:
                await asyncio.sleep(3600)
                yield ""

    (tmp_path / "a.py").write_text("def target(): pass\n")
    gen = ToolThenHang()
    session = AgentSession(str(tmp_path), generator=gen)

    async def run_turn():
        async for _ in session.ask("q1"):
            pass

    task = asyncio.ensure_future(run_turn())
    for _ in range(500):
        if gen.n >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    session.abort_turn()

    assert session.messages[0]["role"] == "system"
    roles = [m["role"] for m in session.messages]
    for i in range(1, len(roles)):
        assert not (roles[i] == roles[i - 1] == "user"), (
            f"consecutive user messages at {i}: {roles}"
        )
    # The turn is closed by an assistant message (see abort_turn's comment
    # on why assistant and not user), so what matters is that it is not a
    # dangling TOOL CALL waiting for a result.
    assert session.messages[-1]["role"] == "assistant"
    assert not session.messages[-1]["content"].startswith("TOOL:")
    assert "interrupted" in session.messages[-1]["content"].lower()


@pytest.mark.asyncio
async def test_abort_turn_handles_interrupt_during_the_very_first_model_call(tmp_path):
    """Third interrupt timing: Ctrl-C before any tool has run at all, so
    history is just [system, user(question)]. The question must still be
    marked abandoned, or the next question inherits it."""

    class HangsImmediately:
        def __init__(self):
            self.calls: list[list[dict]] = []
            self.n = 0

        async def chat(self, messages: list[dict], stream: bool = True):
            self.calls.append([dict(m) for m in messages])
            self.n += 1
            if self.n == 1:
                await asyncio.sleep(3600)
                yield ""
            else:
                yield "4"

    gen = HangsImmediately()
    session = AgentSession(str(tmp_path), generator=gen)

    async def run_turn():
        async for _ in session.ask("how is authentication handled?"):
            pass

    task = asyncio.ensure_future(run_turn())
    for _ in range(500):
        if gen.n >= 1:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    session.abort_turn()
    [_ async for _ in session.ask("what is 2 + 2?")]

    joined = "\n".join(m["content"] for m in gen.calls[-1])
    assert "interrupt" in joined.lower()
    assert gen.calls[-1][-1]["content"] == "what is 2 + 2?"
    roles = [m["role"] for m in session.messages]
    for i in range(1, len(roles)):
        assert not (roles[i] == roles[i - 1] == "user"), f"bad alternation: {roles}"


@pytest.mark.asyncio
async def test_abort_turn_is_a_noop_after_a_normally_completed_turn(tmp_path):
    """abort_turn() must not corrupt a session where nothing was
    interrupted — the REPL only calls it on CancelledError, but a stray
    call (or a future caller) must not inject a spurious 'abandoned' note
    that would make the model refuse to reference its own last answer."""
    gen = FakeGenerator(["a normal answer", "second answer"])
    session = AgentSession(str(tmp_path), generator=gen)

    [_ async for _ in session.ask("q1")]
    before = [dict(m) for m in session.messages]

    session.abort_turn()

    assert session.messages == before, "abort_turn mutated a cleanly-finished turn"

    [_ async for _ in session.ask("q2")]
    joined = "\n".join(m["content"] for m in gen.calls[-1])
    assert "INTERRUPTED" not in joined
