"""Interactive terminal REPL: `sleuth` with no subcommand.

Launches a persistent, multi-turn agentic session scoped to the current
working directory (or an explicit path) — ask multiple questions in one
run, each building on the last, with tool-call activity printed as it
happens rather than only the final answer.

Built as a thin driver around AgentSession (sleuth/retrieve/agent_session.py):
this module owns only the input/output loop, prompt formatting, and
exit/blank-line handling. All actual tool-call/generation logic lives in
AgentSession, shared with the one-shot `sleuth agentic` CLI command.

Visual polish (Claude-Code-style full-screen layout: scrolling transcript
pane above a real bordered input box pinned to the bottom of the terminal)
is built on `prompt_toolkit` — this project's first external runtime
dependency (see requirements.txt). A hand-rolled print()/readline() loop
cannot draw a persistent bordered widget the terminal doesn't scroll away;
that needs a real full-screen terminal application (alternate screen
buffer, its own render loop, raw-mode key handling) which is exactly what
prompt_toolkit's Application/Layout primitives are for. Deliberately using
the low-level building blocks (Application, HSplit/Window, Buffer,
BufferControl, a hand-written Lexer) rather than a heavier full framework
like `textual` — closer to this project's "understand every piece"
philosophy than a batteries-included TUI toolkit would be.

Brand colors and the logo mark shape are ported from the real web app —
web/src/theme.css's storm theme (--bg #0F372F, --accent #ECBC6B) and
web/src/components/Logo.jsx's three-bar mark (top rule / vertical /
horizontal inside a rounded square, same shape as web/public/favicon.svg)
— not invented placeholder colors, so the CLI and the web app read as the
same product.
"""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app, get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from sleuth.config import Config
from sleuth.retrieve.agent_session import AgentSession, AnswerEvent, ToolCallEvent, ToolResultEvent

EXIT_COMMANDS = {"exit", "quit"}

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_INTERVAL = 0.08

# Exact brand hex values from web/src/theme.css's storm (default dark) theme
# — not approximated, so the CLI's palette matches the real web app.
_BG = "#0F372F"
_ACCENT = "#ECBC6B"  # gold — primary brand color (logo mark, buttons, links)
_TEXT = "#F2F5F2"
_TEXT_MUTED = "#9CA9A3"  # approximation of theme.css's rgba(242,245,242,0.58) over --bg
_TEXT_FAINT = "#5C6863"  # approximation of rgba(242,245,242,0.30) over --bg
_STATUS_NEUTRAL = "#C89B6A"  # theme.css's secondary accent (used for tool-call activity)

STYLE = Style.from_dict(
    {
        "": f"fg:{_TEXT}",
        "accent": f"fg:{_ACCENT} bold",
        "muted": f"fg:{_TEXT_MUTED}",
        "dim": f"fg:{_TEXT_FAINT}",
        "tool": f"fg:{_STATUS_NEUTRAL}",
        "user": f"fg:{_TEXT} bold",
        "answer": f"fg:{_TEXT}",
        "divider": f"fg:{_TEXT_FAINT}",
        "frame.border": f"fg:{_ACCENT}",
        "frame.label": f"fg:{_ACCENT} bold",
        # Markdown-lite rendering of the model's final answer (see
        # _render_markdown_line below) — headings/bold/inline-code/bullets
        # get their own look instead of raw '#'/'**'/'`' characters.
        "answer.heading": f"fg:{_ACCENT} bold",
        "answer.bold": f"fg:{_TEXT} bold",
        "answer.code": f"fg:{_STATUS_NEUTRAL}",
        "answer.bullet": f"fg:{_ACCENT}",
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
    lines.append(("Ask questions about this codebase. Type exit or quit to leave.", "class:muted"))
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


class _TranscriptLexer(Lexer):
    """Applies per-line (style, text) fragments to the transcript Buffer's
    text.

    A Buffer/BufferControl only ever shows literal characters — it does not
    interpret embedded ANSI escape codes — so per-line/per-span coloring is
    done the "real" prompt_toolkit way: a Lexer that, for each line number,
    returns the fragments recorded for that line in a parallel list
    maintained alongside the transcript text (falls back to one plain
    fragment covering the whole line when no per-span markup was recorded).
    """

    def __init__(self, get_line_fragments: Callable[[], list[list[tuple[str, str]]]]):
        self._get_line_fragments = get_line_fragments

    def lex_document(self, document: Document):
        fragments = self._get_line_fragments()

        def get_line(lineno: int):
            if lineno < len(fragments):
                return fragments[lineno]
            text = document.lines[lineno] if lineno < len(document.lines) else ""
            return [("", text)]

        return get_line


class _Transcript:
    """Owns the scrolling transcript pane's content: the plain-text line
    list (what the Buffer actually holds, for cursor/scroll math) plus a
    parallel list of per-line (style, text) fragments (what the Lexer
    hands back for rendering — see _TranscriptLexer), kept in sync with a
    read-only Buffer.

    Auto-follows the bottom (tail -f style) as new lines stream in, UNLESS
    the user has manually scrolled up (see scroll()/PageUp/PageDown/Up/Down
    key bindings in run_repl) — a streamed tool-call/answer line must never
    yank the view back down while someone is mid-read of earlier output.
    scroll_to_bottom() (bound to End/Ctrl-End) re-engages auto-follow.
    """

    def __init__(self):
        self.lines: list[str] = []
        self.fragments: list[list[tuple[str, str]]] = []
        self.buffer = Buffer(read_only=True)
        self.following = True

    def _sync(self) -> None:
        text = "\n".join(self.lines)
        cursor_position = len(text) if self.following else min(self.buffer.cursor_position, len(text))
        self.buffer.set_document(Document(text, cursor_position=cursor_position), bypass_readonly=True)
        app = get_app_or_none()
        if app is not None:
            app.invalidate()

    def append(self, text: str, style: str = "") -> None:
        for line in text.split("\n"):
            self.lines.append(line)
            self.fragments.append([(style, line)] if style else [])
        self._sync()

    def append_lines(self, pairs: list[tuple[str, str]]) -> None:
        for text, style in pairs:
            self.lines.append(text)
            self.fragments.append([(style, text)] if style else [])
        self._sync()

    def append_markdown(self, text: str, base_style: str = "class:answer", prefix: list[tuple[str, str]] | None = None) -> None:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            self.lines.append((("".join(t for _, t in prefix) if prefix and i == 0 else "")) + line)
            rendered = _render_markdown_line(line, base_style)
            if prefix is not None and i == 0:
                rendered = prefix + rendered
            self.fragments.append(rendered)
        self._sync()

    def replace_last(self, text: str, style: str) -> None:
        self.lines[-1] = text
        self.fragments[-1] = [(style, text)] if style else []
        self._sync()

    def pop(self) -> None:
        self.lines.pop()
        self.fragments.pop()
        self._sync()

    def scroll(self, lines: int) -> None:
        # Any manual scroll disengages auto-follow immediately, even a
        # scroll-down — the user might be paging back down toward the
        # bottom deliberately, and re-pinning early would fight their next
        # keypress. Only scroll_to_bottom() explicitly re-engages it.
        self.following = False
        target = self.buffer.document.translate_row_col_to_index(
            max(0, self.buffer.document.cursor_position_row + lines), 0
        )
        self.buffer.cursor_position = target

    def page_scroll(self, page_lines: int, down: bool) -> None:
        self.scroll(page_lines if down else -page_lines)

    def scroll_to_bottom(self) -> None:
        self.following = True
        self._sync()


class _Spinner:
    """A "thinking" indicator implemented as a transcript line that gets
    its text replaced on a timer, then removed once stopped — the
    full-screen-app equivalent of the old carriage-return spinner.
    """

    def __init__(self, transcript: _Transcript):
        self._transcript = transcript
        self._task: asyncio.Task | None = None
        self._owns_line = False

    async def _spin(self) -> None:
        self._transcript.append("", "class:dim")
        self._owns_line = True
        i = 0
        try:
            while True:
                frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
                self._transcript.replace_last(f"{frame} thinking...", "class:dim")
                i += 1
                await asyncio.sleep(_SPINNER_INTERVAL)
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._spin())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_line:
            self._transcript.pop()
            self._owns_line = False


async def run_repl(
    path: str,
    config: Config,
    input: Input | None = None,
    output: Output | None = None,
    session_factory: Callable[[str, Config], AgentSession] | None = None,
) -> str:
    """Run the full-screen interactive session until the user exits.

    Returns the final transcript text (mainly useful for tests). input/
    output/session_factory are injectable purely for testing — real usage
    from sleuth/cli.py leaves them at their real-terminal/AgentSession
    defaults. Tests drive `input` with prompt_toolkit's own
    `create_pipe_input()` and pass `output=DummyOutput()`.
    """
    factory = session_factory if session_factory is not None else _default_session_factory
    resolved_root = Path(path).resolve()
    session = factory(path, config)

    transcript = _Transcript()
    transcript.append_lines(_banner_lines(resolved_root, _agentic_model_name(config)))
    transcript.append("")

    spinner = _Spinner(transcript)
    # Serializes turns strictly in submission order: each new turn's
    # runner awaits whatever turn was already pending before doing its
    # own work. This makes "type a question, then immediately type exit"
    # behave correctly (exit waits for the in-flight answer) both for a
    # real user (who naturally waits anyway) and for tests that pipe in
    # several lines of input back-to-back with no real delay between them.
    pending: dict[str, asyncio.Task | None] = {"task": None}

    def schedule(coro_factory: Callable[[], "asyncio.Future"]) -> None:
        previous = pending["task"]

        async def runner():
            if previous is not None:
                await previous
            await coro_factory()

        pending["task"] = get_app().create_background_task(runner())

    async def process_question(question: str) -> None:
        transcript.append(f"❯ {question}", "class:user")
        spinner.start()
        first_event = True
        async for event in session.ask(question):
            if first_event:
                await spinner.stop()
                first_event = False
            if isinstance(event, ToolCallEvent):
                transcript.append(_format_tool_call(event), "class:tool")
                spinner.start()
            elif isinstance(event, ToolResultEvent):
                transcript.append(_format_tool_result(event), "class:dim")
            elif isinstance(event, AnswerEvent):
                transcript.append_markdown(event.text, prefix=[("class:accent", "● ")])
                if event.truncated:
                    transcript.append(
                        "(Note: search was cut short after reaching the iteration limit.)", "class:dim"
                    )
        await spinner.stop()
        transcript.append("")

    async def do_exit() -> None:
        get_app().exit(result="\n".join(transcript.lines))

    def handle_submit(buff: Buffer) -> bool:
        question = buff.text.strip()
        if not question:
            return False
        if question.lower() in EXIT_COMMANDS:
            schedule(do_exit)
            return False
        schedule(lambda question=question: process_question(question))
        return False

    input_area = TextArea(
        height=1,
        multiline=False,
        wrap_lines=False,
        accept_handler=handle_submit,
        style="class:user",
    )

    transcript_window = Window(
        content=BufferControl(buffer=transcript.buffer, lexer=_TranscriptLexer(lambda: transcript.fragments)),
        wrap_lines=True,
        allow_scroll_beyond_bottom=True,
    )

    kb = KeyBindings()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event) -> None:
        schedule(do_exit)

    # Manual scrolling of the transcript pane — Up/Down move one line,
    # PageUp/PageDown move a full screenful, End/Ctrl-End jump back to the
    # live tail. Bound at the application level (not on the input
    # TextArea's own buffer) so scrolling works no matter which widget has
    # focus, and specifically does NOT consume the TextArea's own
    # up/down-through-input-history behavior — prompt_toolkit's default
    # up/down bindings only apply within a focused multiline buffer, and
    # this REPL's input box is single-line, so these are otherwise unused.
    @kb.add("up")
    def _(event) -> None:
        transcript.scroll(-1)

    @kb.add("down")
    def _(event) -> None:
        transcript.scroll(1)

    @kb.add("pageup")
    def _(event) -> None:
        transcript.page_scroll(transcript_window.render_info.window_height if transcript_window.render_info else 10, down=False)

    @kb.add("pagedown")
    def _(event) -> None:
        transcript.page_scroll(transcript_window.render_info.window_height if transcript_window.render_info else 10, down=True)

    @kb.add("end")
    @kb.add("c-end")
    def _(event) -> None:
        transcript.scroll_to_bottom()

    root = HSplit(
        [
            transcript_window,
            Window(height=1, char="─", style="class:divider"),
            Frame(input_area, title="❯ ask · enter to send · ↑↓ scroll · pgup/pgdn page · end jump to bottom · ctrl-c quit"),
        ]
    )

    application: Application[str] = Application(
        layout=Layout(root, focused_element=input_area),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
        mouse_support=False,
        input=input,
        output=output,
    )

    result = await application.run_async()
    return result if result is not None else "\n".join(transcript.lines)
