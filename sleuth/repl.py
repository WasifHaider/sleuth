"""Interactive terminal REPL: `sleuth` with no subcommand.

Launches a persistent, multi-turn agentic session scoped to the current
working directory (or an explicit path) — ask multiple questions in one
run, each building on the last, with tool-call activity printed as it
happens rather than only the final answer.

Built as a thin driver around AgentSession (sleuth/retrieve/agent_session.py):
this module owns only the input/output loop, prompt formatting, and
exit/blank-line/interrupt handling. All actual tool-call/generation logic
lives in AgentSession, shared with the one-shot `sleuth agentic` CLI command.

Rendering model: SCROLLBACK, not a full-screen TUI. Earlier versions of
this REPL were a `full_screen=True` prompt_toolkit Application with a
bordered input box pinned to the bottom and a hand-written scrolling
transcript pane. That looked nice and did not work: the alternate screen
buffer takes scrolling away from the terminal, so mouse/touchpad scroll,
text selection and copy all had to be reimplemented by hand — and the
hand-rolled version fought prompt_toolkit's own cursor-driven auto-scroll
(Window._scroll() recomputes vertical_scroll from the cursor on every
repaint, and a transcript pinned to its own tail therefore snapped the
view back to the bottom on every redraw).

Claude Code, Codex and Hermes all avoid that class of bug by NOT taking
over the screen: the transcript is printed into the terminal's normal
scrollback and only the input line is a prompt_toolkit widget. Scrolling
(including two-finger touchpad scroll), selection, copy and the
terminal's own search then work because the terminal is doing them, not
us. That is what this module now does — a `PromptSession` for input plus
`print_formatted_text` for output. The tradeoff, accepted deliberately:
the input line scrolls away with the content instead of being pinned in
a permanent bordered frame.

Brand colors and the logo mark shape are ported from the real web app —
web/src/theme.css's storm theme (--bg #0F372F, --accent #ECBC6B) and
web/src/components/Logo.jsx's three-bar mark (top rule / vertical /
horizontal inside a rounded square, same shape as web/public/favicon.svg)
— not invented placeholder colors, so the CLI and the web app read as the
same product.
"""

import asyncio
import re
import signal
import time
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style

from sleuth.config import Config
from sleuth.retrieve.agent_session import AgentSession, AnswerEvent, ToolCallEvent, ToolResultEvent

EXIT_COMMANDS = {"exit", "quit"}

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_INTERVAL = 0.1

# Exact brand hex values from web/src/theme.css's storm (default dark) theme
# — not approximated, so the CLI's palette matches the real web app.
#
# Note there is deliberately no `bg:` anywhere in this style: the terminal's
# own (possibly transparent / user-themed) background is left alone and only
# foreground text is colored.
_ACCENT = "#ECBC6B"  # gold — primary brand color (logo mark, buttons, links)
_TEXT = "#F2F5F2"
_TEXT_MUTED = "#9CA9A3"  # approximation of theme.css's rgba(242,245,242,0.58) over --bg
_TEXT_FAINT = "#5C6863"  # approximation of rgba(242,245,242,0.30) over --bg
_STATUS_NEUTRAL = "#C89B6A"  # theme.css's secondary accent (used for tool-call activity)

STYLE = Style.from_dict(
    {
        "accent": f"fg:{_ACCENT} bold",
        "muted": f"fg:{_TEXT_MUTED}",
        "dim": f"fg:{_TEXT_FAINT}",
        "tool": f"fg:{_STATUS_NEUTRAL}",
        "user": f"fg:{_TEXT} bold",
        "answer": f"fg:{_TEXT}",
        # Markdown-lite rendering of the model's final answer (see
        # _render_markdown_line below) — headings/bold/inline-code/bullets
        # get their own look instead of raw '#'/'**'/'`' characters.
        "answer.heading": f"fg:{_ACCENT} bold",
        "answer.bold": f"fg:{_TEXT} bold",
        "answer.code": f"fg:{_STATUS_NEUTRAL}",
        "answer.bullet": f"fg:{_ACCENT}",
        # The input prompt itself.
        "prompt": f"fg:{_ACCENT} bold",
    }
)

# Large block wordmark, built from a real per-letter 5-row block font
# (not freehand ASCII art) so it legibly spells SLEUTH — same visual
# weight as Claude Code's/Hermes's own startup banners. Plain
# block-drawing characters (█), no figlet-style font dependency, matching
# this project's no-vendor-SDK philosophy.
_BLOCK_FONT = {
    "S": [" ███ ", "█    ", " ███ ", "    █", " ███ "],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
}


def _render_wordmark(word: str, spacing: int = 1) -> list[str]:
    rows = ["" for _ in range(5)]
    for ch in word:
        glyph = _BLOCK_FONT[ch]
        for i in range(5):
            rows[i] += glyph[i] + " " * spacing
    return rows


_WORDMARK_ROWS = _render_wordmark("SLEUTH")

# The real logo mark (web/src/components/Logo.jsx / favicon.svg): a
# rounded square containing a top rule, a vertical bar, and a horizontal
# bar — rendered here as a small ASCII glyph using the same three strokes.
_LOGO_MARK_ROWS = [
    "╭────╮",
    "│▔▔▔▔│",
    "│▏   │",
    "│▏▁▁ │",
    "╰────╯",
]

_HELP_LINE = (
    "Ask questions about this codebase. "
    "enter send · ↑↓ history · ctrl-c interrupt · exit/quit or ctrl-d leave"
)


def _default_session_factory(path: str, config: Config) -> AgentSession:
    return AgentSession(path, config=config)


def _agentic_model_name(config: Config) -> str:
    # Purely cosmetic (shown in the banner) — avoids constructing a real
    # generator just to display which model backs this session. Falls
    # back to a generic label if config is None (tests stub the session
    # entirely and never reach real generator construction).
    if config is None:
        return "(unconfigured)"
    from sleuth.retrieve.agent_session import AGENTIC_GROQ_MODEL

    return AGENTIC_GROQ_MODEL


def _banner_lines(resolved_root: Path, model_name: str) -> list[tuple[str, str]]:
    """Returns (text, style_class) pairs, one per rendered line."""
    lines: list[tuple[str, str]] = []
    for i in range(len(_LOGO_MARK_ROWS)):
        wordmark_row = _WORDMARK_ROWS[i] if i < len(_WORDMARK_ROWS) else ""
        lines.append((f"{_LOGO_MARK_ROWS[i]}  {wordmark_row}", "class:accent"))
    rule = "─" * 60
    lines.append((rule, "class:dim"))
    lines.append((f"dir:   {resolved_root}", "class:muted"))
    lines.append((f"model: {model_name}", "class:muted"))
    lines.append((rule, "class:dim"))
    lines.append(("", ""))
    lines.append((_HELP_LINE, "class:muted"))
    lines.append(("", ""))
    return lines


def _format_tool_call(event: ToolCallEvent) -> str:
    args_str = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
    return f"  ⚙ {event.name}({args_str})"


def _format_tool_result(event: ToolResultEvent) -> str:
    # Tool output can be long (up to 50 grep matches / 400 file lines) —
    # keep the transcript scannable by showing only a short preview
    # rather than dumping everything the model saw back onto the screen.
    preview = event.result if len(event.result) <= 300 else event.result[:300] + " ... (truncated for display)"
    lines = preview.splitlines() or ["(empty result)"]
    return "\n".join(f"     {line}" for line in lines)


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)([-*])\s+(.*)$")


def _render_inline_markdown(text: str, base_style: str) -> list[tuple[str, str]]:
    """Splits **bold** and `code` spans out of one line of text into
    (style, text) fragments, leaving everything else as base_style."""
    fragments: list[tuple[str, str]] = []
    pos = 0
    # Interleave the two inline patterns by scanning for whichever comes
    # first at each step, rather than running one pass then the other —
    # otherwise a **bold `code`** span would have its inner backticks
    # missed once the bold pass has already consumed the whole match.
    pattern = re.compile(f"{_BOLD_RE.pattern}|{_INLINE_CODE_RE.pattern}")
    for match in pattern.finditer(text):
        if match.start() > pos:
            fragments.append((base_style, text[pos : match.start()]))
        if match.group(1) is not None:  # **bold**
            fragments.append(("class:answer.bold", match.group(1)))
        else:  # `code`
            fragments.append(("class:answer.code", match.group(2)))
        pos = match.end()
    if pos < len(text):
        fragments.append((base_style, text[pos:]))
    return fragments or [(base_style, text)]


def _render_markdown_line(line: str, base_style: str) -> list[tuple[str, str]]:
    """Renders one line of the model's answer as (style, text) fragments —
    a deliberately small markdown-lite subset (#/##/### headings, **bold**,
    `inline code`, -/* bullets), not a full CommonMark parser. Model
    answers are prose paragraphs and short lists, never tables or nested
    blockquotes, so this covers everything that actually shows up without
    pulling in a real markdown dependency.
    """
    heading_match = _HEADING_RE.match(line)
    if heading_match:
        return _render_inline_markdown(heading_match.group(2), "class:answer.heading")

    bullet_match = _BULLET_RE.match(line)
    if bullet_match:
        indent, _, rest = bullet_match.groups()
        return [(base_style, f"{indent}"), ("class:answer.bullet", "• ")] + _render_inline_markdown(
            rest, base_style
        )

    return _render_inline_markdown(line, base_style)


class _Transcript:
    """Prints transcript lines straight into the terminal's scrollback and
    keeps a plain-text copy of everything printed.

    The plain-text copy exists for two reasons: run_repl() returns it (the
    tests assert against it, since a DummyOutput swallows the real render),
    and it is what makes this class trivially testable compared to the old
    full-screen Buffer/Lexer pair it replaces.

    There is intentionally no scrolling logic here at all — the terminal
    owns scrolling now (see this module's docstring).
    """

    def __init__(self, output: Output | None = None):
        self.lines: list[str] = []
        self._output = output

    def _emit(self, fragments: list[tuple[str, str]]) -> None:
        self.lines.append("".join(text for _, text in fragments))
        print_formatted_text(
            FormattedText(fragments),
            style=STYLE,
            output=self._output,
        )

    def append(self, text: str, style: str = "") -> None:
        for line in text.split("\n"):
            self._emit([(style, line)])

    def append_lines(self, pairs: list[tuple[str, str]]) -> None:
        for text, style in pairs:
            self._emit([(style, text)])

    def append_markdown(
        self,
        text: str,
        base_style: str = "class:answer",
        prefix: list[tuple[str, str]] | None = None,
    ) -> None:
        for i, line in enumerate(text.split("\n")):
            rendered = _render_markdown_line(line, base_style)
            if prefix is not None and i == 0:
                rendered = prefix + rendered
            self._emit(rendered)

    def record_question(self, question: str) -> None:
        # The prompt_toolkit prompt has already drawn "❯ <question>" into
        # the scrollback as part of accepting the input, so re-printing it
        # would duplicate it on screen. Only the plain-text copy needs it.
        self.lines.append(f"❯ {question}")


class _Spinner:
    """A "thinking" indicator drawn in place on the current terminal line.

    Because the transcript is plain scrollback now, this can be a real
    single-line, redraw-in-place spinner (write → cursor_backward →
    erase_end_of_line → write) instead of the old version's trick of
    appending a transcript line and mutating it, which corrupted the
    transcript whenever a line was appended between start() and stop().

    start() is safe to call repeatedly: an already-running spinner is left
    alone rather than starting a second task that would fight the first.
    Every draw includes elapsed seconds, so a slow model call (agentic
    turns are non-streaming and can take tens of seconds) visibly ticks
    instead of looking frozen.
    """

    def __init__(self, output: Output | None = None):
        self._output = output
        self._task: asyncio.Task | None = None
        self._width = 0

    def _write(self, text: str) -> None:
        if self._output is None:
            return
        if self._width:
            self._output.cursor_backward(self._width)
            self._output.erase_end_of_line()
        self._output.write(text)
        self._output.flush()
        self._width = len(text)

    def _clear(self) -> None:
        if self._output is None or not self._width:
            self._width = 0
            return
        self._output.cursor_backward(self._width)
        self._output.erase_end_of_line()
        self._output.flush()
        self._width = 0

    async def _spin(self) -> None:
        started = time.monotonic()
        i = 0
        try:
            while True:
                frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
                elapsed = time.monotonic() - started
                self._write(f"{frame} thinking… {elapsed:.0f}s (ctrl-c to interrupt)")
                i += 1
                await asyncio.sleep(_SPINNER_INTERVAL)
        except asyncio.CancelledError:
            raise
        finally:
            self._clear()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._spin())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._clear()


async def run_repl(
    path: str,
    config: Config,
    input: Input | None = None,
    output: Output | None = None,
    session_factory: Callable[[str, Config], AgentSession] | None = None,
) -> str:
    """Run the interactive session until the user exits.

    Returns the full transcript text (mainly useful for tests). input/
    output/session_factory are injectable purely for testing — real usage
    from sleuth/cli.py leaves them at their real-terminal/AgentSession
    defaults. Tests drive `input` with prompt_toolkit's own
    `create_pipe_input()` and pass `output=DummyOutput()`.
    """
    factory = session_factory if session_factory is not None else _default_session_factory
    resolved_root = Path(path).resolve()
    session = factory(path, config)

    transcript = _Transcript(output=output)
    transcript.append_lines(_banner_lines(resolved_root, _agentic_model_name(config)))

    spinner = _Spinner(output=output)

    # Ctrl-C must never be swallowed by the prompt widget while a turn is
    # in flight — see _run_turn_interruptibly below, which is where the
    # real interrupt handling lives.
    kb = KeyBindings()

    prompt_session: PromptSession[str] = PromptSession(
        message=FormattedText([("class:prompt", "❯ ")]),
        style=STYLE,
        history=InMemoryHistory(),
        multiline=False,
        key_bindings=kb,
        input=input,
        output=output,
    )

    async def process_question(question: str) -> None:
        spinner.start()
        try:
            async for event in session.ask(question):
                await spinner.stop()
                if isinstance(event, ToolCallEvent):
                    transcript.append(_format_tool_call(event), "class:tool")
                    # Back to "thinking" while the tool runs and the model
                    # decides what to do with the result.
                    spinner.start()
                elif isinstance(event, ToolResultEvent):
                    transcript.append(_format_tool_result(event), "class:dim")
                    spinner.start()
                elif isinstance(event, AnswerEvent):
                    transcript.append_markdown(event.text, prefix=[("class:accent", "● ")])
                    if event.truncated:
                        transcript.append(
                            "(Note: search was cut short after reaching the iteration limit.)",
                            "class:dim",
                        )
        finally:
            await spinner.stop()
        transcript.append("")

    async def run_turn_interruptibly(question: str) -> None:
        """Runs one turn as a cancellable task with SIGINT wired to cancel it.

        Without this, Ctrl-C during a turn does nothing: the prompt widget
        isn't reading input at that point, so prompt_toolkit's own
        KeyboardInterrupt handling is not in play, and an agentic turn can
        legitimately occupy several minutes (up to MAX_ITERATIONS
        non-streaming model calls, each with its own HTTP timeout plus one
        retry). The old REPL queued Ctrl-C *behind* the in-flight turn,
        which made it unquittable for exactly that whole window.
        """
        task = asyncio.ensure_future(process_question(question))

        def on_sigint(_signum, _frame) -> None:
            task.cancel()

        previous_handler = None
        installed = False
        try:
            # signal.signal() only works on the main thread, and SIGINT is
            # not installable at all under some embedded/threaded hosts —
            # degrade to "not interruptible" rather than crashing there.
            previous_handler = signal.signal(signal.SIGINT, on_sigint)
            installed = True
        except (ValueError, OSError):
            pass

        try:
            await task
        except asyncio.CancelledError:
            await spinner.stop()
            # Tell the session its turn was abandoned. Without this, the
            # interrupted question stays in the message history with no
            # answer, and the model finishes answering IT on the next
            # question instead of the new one — see
            # AgentSession.abort_turn() for the full explanation.
            abort = getattr(session, "abort_turn", None)
            if callable(abort):
                abort()
            transcript.append("  ⏹ interrupted", "class:dim")
            transcript.append("")
        finally:
            if installed:
                signal.signal(signal.SIGINT, previous_handler)

    while True:
        try:
            question = await prompt_session.prompt_async()
        except KeyboardInterrupt:
            # Ctrl-C at an empty prompt: clear the line and keep going,
            # matching Claude Code / a normal shell. Ctrl-D is the way out.
            continue
        except EOFError:
            break

        question = question.strip()
        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            transcript.record_question(question)
            break

        transcript.record_question(question)
        await run_turn_interruptibly(question)

    return "\n".join(transcript.lines)
