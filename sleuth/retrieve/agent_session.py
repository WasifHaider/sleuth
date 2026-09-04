"""AgentSession: a reusable multi-turn agentic tool loop.

Extracted from sleuth/retrieve/agentic.py so the same tool-call machinery
(text-protocol parsing, grep/list_files/read_file tools, fallback-chain
generation) can back three different callers: the CLI's one-shot
`sleuth agentic` command, the interactive terminal REPL (Phase 2), and —
as a possible future follow-on — ingest-time repo-summary generation.

The key difference from the old one-shot run_agentic(): `messages` lives on
the AgentSession instance and survives across calls to `ask()`, so a
second question can build on everything the first question's tool calls
and answer already established, instead of starting from a blank slate
every time.
"""

import json
import re
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from sleuth.config import Config
from sleuth.llm.generate import GroqGenerator, Generator, NimGenerator, chat_with_fallback

UNAVAILABLE_MESSAGE = (
    "The model backend is unavailable right now (all configured generators failed). "
    "Please try again in a moment."
)

# Agentic mode needs a model that reliably follows a plain-text tool-call
# protocol (no native function-calling API in play — see SYSTEM_PROMPT
# below). Empirically checked against Groq's catalog (2026-08-31):
# openai/gpt-oss-120b (the shared config.groq_model default used for
# regular Q&A) and openai/gpt-oss-20b both threw 400 output_parse_failed on
# this exact prompt shape; groq/compound-mini hit 429 rate limits on the
# free tier before a fair test; qwen/qwen3.8-27b reliably emitted a
# well-formed `TOOL: name {"arg": "value"}` line (3/3) once the prompt
# included one concrete example call. Deliberately NOT tied to
# config.groq_model — that field is tuned for regular chat/generation
# quality, a different concern from "will this model follow a strict text
# protocol," so overriding it here would silently degrade whichever this
# constant isn't tuned for.
AGENTIC_GROQ_MODEL = "qwen/qwen3.8-27b"

MAX_ITERATIONS = 6
GREP_MAX_MATCHES = 50
LIST_FILES_MAX_RESULTS = 200
READ_FILE_MAX_LINES = 400
# read_file's line cap alone assumes lines are short/code-like — a
# 217-line prose file (CLAUDE.md, long paragraphs) passed that cap cleanly
# while still totaling 32,235 bytes, and feeding that whole block back as
# one tool result pushed the running conversation over Groq's response
# timeout on the NEXT call (confirmed live: a plain ReadTimeout on both the
# primary and NIM fallback generator, not a 429/rate-limit — see
# AGENTIC_GENERATOR_TIMEOUT_SECONDS below for the other half of this fix).
# A char cap closes the gap the line cap alone leaves open for any
# prose-heavy file, not just this one repo's CLAUDE.md.
READ_FILE_MAX_CHARS = 8000

# Globs that match everything, and are therefore no-ops as *filters*. They
# are anything but no-ops to ripgrep, though: passing any explicit --glob
# switches rg out of "respect .gitignore" mode for matching, so a model
# reflexively adding glob="*" to a grep call silently drags every ignored
# directory back into the search. Measured on this repo: `--glob '*'`
# scanned 12,853 files under .venv/ and produced 2,440,969 bytes of output
# where the same search with no glob produced 5,815 bytes from 0 ignored
# files — a 420x blowup of third-party code the user never wrote, which
# also floods the model's context and (on Windows) drags in files whose
# bytes break subprocess decoding.
#
# Dropping such a glob is semantically identical to honoring it (it
# excludes nothing either way) while restoring ignore filtering. Verified:
# no glob at all lists 184 files here, and a REAL glob like "*.py" still
# filters correctly (75 files, 0 of them under .venv) — this only discards
# match-everything patterns.
#
# Deliberately a no-op-glob check rather than a hand-maintained exclusion
# list (--glob '!.venv' --glob '!node_modules'): that approach is
# whack-a-mole and was measured failing on this very repo, where those two
# negations still let 7,202 files through from .venv-win/, a third venv
# directory nobody thought to add. .gitignore already knows every
# ignorable directory; the fix is to stop overriding it.
_MATCH_EVERYTHING_GLOBS = {"*", "**", "**/*", "*/*", ".", "./*"}


def _normalize_glob(glob: str | None) -> str | None:
    """Drops globs that match everything (see _MATCH_EVERYTHING_GLOBS)."""
    if glob is None:
        return None
    if glob.strip() in _MATCH_EVERYTHING_GLOBS:
        return None
    return glob


def _run_tool_subprocess(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """subprocess.run() for the agentic tools, with decoding pinned to UTF-8.

    text=True alone decodes with the *locale* encoding, which on native
    Windows Python is cp1252 — a 256-character codepage. Source files are
    UTF-8, so a single curly quote (\\xe2\\x80\\x9c) in a matched line raises
    UnicodeDecodeError... on the background reader thread subprocess spawns
    (subprocess.py's _readerthread), NOT in the calling code. The result is
    a raw traceback printed over the REPL's output while subprocess.run()
    itself returns "successfully" with the output silently lost, so no
    caller can even detect the failure. Reproduced exactly on this repo.

    errors="replace" is the second half of the fix: pinning UTF-8 alone
    still raises on genuinely non-UTF-8 bytes (a latin-1 file, a binary
    blob rg didn't classify as binary). A tool feeding text to an LLM wants
    a replacement character, never an exception.
    """
    return subprocess.run(
        args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )

# Multi-turn sessions (unlike the old one-shot run_agentic) can accumulate
# messages forever across many questions. Cap the list and drop the oldest
# assistant/tool-result pairs first — they're the bulkiest and least
# reusable content once several questions have passed — while always
# keeping the system prompt (index 0) and enough recent history for the
# model to stay coherent. Kept small on purpose here (default is generous;
# tests use a small override) since the real cost that matters is prompt
# size sent to the model, not memory.
DEFAULT_MAX_MESSAGES = 60

SYSTEM_PROMPT = (
    "You are a code assistant investigating a local codebase to answer the user's "
    "question. You have three tools:\n"
    "  grep(pattern, glob=null) -- regex search across files, first 50 matches\n"
    "  list_files(glob) -- list files matching a glob\n"
    "  read_file(path, start_line=null, end_line=null) -- read up to 400 lines of a file\n"
    "To call a tool, respond with EXACTLY one line in this form and nothing else:\n"
    'TOOL: <tool_name> {"arg": "value", ...}\n'
    "Example of a correct tool call (args must be a valid JSON object with "
    "double-quoted keys/values):\n"
    'TOOL: grep {"pattern": "def foo", "glob": "*.py"}\n'
    "Do not add explanation before or after the TOOL: line when calling a tool.\n"
    "When you have enough information to answer, respond with your final answer as "
    "plain prose (no TOOL: prefix)."
)

_TOOL_LINE_RE = re.compile(r"^TOOL:\s*(\w+)\s*(\{.*\})\s*$", re.MULTILINE)


@dataclass
class ToolCallEvent:
    """The model decided to call a tool. Yielded before the tool runs, so a
    REPL can print "-> grep(pattern=...)" while it's still in flight."""

    name: str
    args: dict


@dataclass
class ToolResultEvent:
    """The tool has finished running. Carries its raw text result."""

    name: str
    result: str


@dataclass
class AnswerEvent:
    """The model produced a final, plain-prose answer for this question."""

    text: str
    truncated: bool = False


AgentEvent = ToolCallEvent | ToolResultEvent | AnswerEvent


class MalformedToolCall(Exception):
    """Raised internally when a line matches the TOOL: <name> {...} shape
    but the {...} isn't valid JSON — distinct from "no tool call at all"
    (a real plain-prose answer) so the caller can retry instead of
    silently showing the broken line to the user as if it were the answer.
    """

    def __init__(self, raw_text: str, parse_error: str):
        self.raw_text = raw_text
        self.parse_error = parse_error
        super().__init__(f"malformed tool call: {parse_error}")


def _parse_tool_call(text: str) -> tuple[str, dict] | None:
    match = _TOOL_LINE_RE.search(text.strip())
    if not match:
        return None
    try:
        args = json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise MalformedToolCall(raw_text=match.group(0), parse_error=str(exc)) from exc
    return match.group(1), args


def _tool_grep(root: Path, pattern: str, glob: str | None = None) -> str:
    # Shelled out to ripgrep (rg) rather than a hand-rolled Python walk:
    # rg respects .gitignore automatically (node_modules/.venv/dist/build
    # excluded for free, no hand-maintained exclusion list needed) and is a
    # real, well-tested regex/encoding/binary-detection implementation.
    # shell=False + an argument list (never a formatted shell string) is
    # deliberate — a model-supplied pattern must never be interpretable as
    # shell syntax.
    args = ["rg", "--line-number", "--no-heading", "--color", "never", "--no-require-git"]
    glob = _normalize_glob(glob)
    if glob:
        args += ["--glob", glob]
    args += [pattern, str(root)]
    try:
        proc = _run_tool_subprocess(args, cwd=root)
    except FileNotFoundError:
        return "(error: ripgrep (rg) is not installed or not on PATH)"

    lines = [_relativize_rg_line(line, root) for line in proc.stdout.splitlines()]

    if proc.returncode == 2 and not lines:
        # rg's own convention: exit 2 means a real error (bad regex, bad
        # glob, unreadable path, ...) — never raise on model-supplied input,
        # feed the error back as a tool result so the model can retry.
        return f"(invalid pattern or rg error: {proc.stderr.strip()})"
    if proc.returncode not in (0, 1, 2):
        return f"(rg exited with code {proc.returncode}: {proc.stderr.strip()})"

    if not lines:
        return "(no matches)"

    # returncode 2 WITH matches on stdout is a partial success, not a
    # failure: rg found real results but also hit at least one path it
    # couldn't read. That is routine here — .venv/lib64 is a WSL-created
    # symlink Windows rg can't traverse ("os error 1920"), as are npm's
    # node_modules/.bin/* links. Previously any exit 2 discarded ~195KB of
    # legitimate matches and told the model "invalid pattern", so the model
    # kept retrying a search that had actually worked, burning its whole
    # MAX_ITERATIONS budget — the REPL's "gets stuck on a codebase
    # question" symptom. Return the matches and note the warning instead.
    note = ""
    if proc.returncode == 2:
        skipped = len([ln for ln in proc.stderr.splitlines() if ln.strip()])
        note = f"\n(note: {skipped} path(s) could not be read and were skipped; matches above are complete for the rest)"

    if len(lines) > GREP_MAX_MATCHES:
        return "\n".join(lines[:GREP_MAX_MATCHES]) + "\n... (truncated at 50 matches)" + note
    return "\n".join(lines) + note


_RG_LINE_RE = re.compile(r"^(?P<path>.*?):(?P<lineno>\d+):(?P<content>.*)$", re.DOTALL)


def _relativize_rg_line(line: str, root: Path) -> str:
    # rg's --no-heading output is "absolute/path:lineno:content" — reformat
    # the leading path relative to root to match the tool's documented
    # "path:lineno: line text" contract (and avoid leaking the host's
    # absolute filesystem layout into the model's context).
    #
    # Parsed with a regex anchored on the ":<digits>:" separator rather than
    # line.split(":", 2). A plain split breaks on Windows, where an
    # absolute path carries its own drive-letter colon:
    # "C:\repo\a.py:1:x" split into 3 parts yields path="C",
    # lineno="\repo\a.py", content="1:x" — so relative_to() always failed,
    # every path stayed absolute, and the line the model was asked to cite
    # came out mangled. Non-greedy .*? keeps the FIRST ":<digits>:" as the
    # separator, which is correct because rg emits the line number
    # immediately after the path.
    match = _RG_LINE_RE.match(line)
    if not match:
        return line
    path_str, lineno, content = match.group("path"), match.group("lineno"), match.group("content")
    try:
        rel = Path(path_str).relative_to(root)
    except ValueError:
        rel = Path(path_str)
    return f"{rel}:{lineno}: {content.strip()}"


def _tool_list_files(root: Path, glob: str) -> str:
    args = ["rg", "--files", "--no-require-git"]
    # Same no-op-glob handling as _tool_grep: list_files(glob="*") is the
    # single most likely call a model makes to "see the project", and with
    # an explicit --glob that enumerated 12,853 .venv files here instead of
    # the repo's own 184.
    normalized = _normalize_glob(glob)
    if normalized:
        args += ["--glob", normalized]
    try:
        proc = _run_tool_subprocess(args, cwd=root)
    except FileNotFoundError:
        return "(error: ripgrep (rg) is not installed or not on PATH)"

    paths = sorted(proc.stdout.splitlines())

    # Same partial-success handling as _tool_grep: unreadable symlinks make
    # rg exit 2 even when it successfully listed everything else.
    if proc.returncode == 2 and not paths:
        return f"(invalid glob or rg error: {proc.stderr.strip()})"
    if proc.returncode not in (0, 1, 2):
        return f"(rg exited with code {proc.returncode}: {proc.stderr.strip()})"

    if not paths:
        return "(no files matched)"

    note = ""
    if proc.returncode == 2:
        skipped = len([ln for ln in proc.stderr.splitlines() if ln.strip()])
        note = f"\n(note: {skipped} path(s) could not be read and were skipped)"

    if len(paths) > LIST_FILES_MAX_RESULTS:
        return "\n".join(paths[:LIST_FILES_MAX_RESULTS]) + "\n... (truncated at 200 files)" + note
    return "\n".join(paths) + note


def _tool_read_file(root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    # Path-traversal guard: resolve the joined path and verify it's still
    # under root before ever touching the filesystem — root / "../x" (or an
    # absolute path override) must not escape the sandboxed directory.
    resolved_root = root.resolve()
    target = (root / path).resolve()
    if not (target == resolved_root or resolved_root in target.parents):
        return f"(error: path {path!r} escapes the sandboxed root directory)"
    if not target.is_file():
        return f"(error reading {path}: not a file)"

    start = max((start_line or 1), 1)
    # sed -n '<start>,<end>p' pulls the requested range in the shell tool
    # itself (consistent with grep/list_files shelling out too) rather than
    # reading the whole file into Python and slicing. end is still capped
    # at start + READ_FILE_MAX_LINES regardless of what was requested.
    requested_end = end_line if end_line is not None else start + READ_FILE_MAX_LINES - 1
    end = min(requested_end, start + READ_FILE_MAX_LINES - 1)
    try:
        proc = _run_tool_subprocess(
            ["sed", "-n", f"{start},{end}p", str(target)],
        )
    except FileNotFoundError:
        return "(error: sed is not installed or not on PATH)"
    if proc.returncode != 0:
        return f"(error reading {path}: {proc.stderr.strip()})"

    lines = proc.stdout.splitlines()
    text = "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))
    if len(text) > READ_FILE_MAX_CHARS:
        # Truncate on a line boundary where possible rather than mid-line,
        # so the model doesn't see a line cut off mid-word/mid-token.
        truncated = text[:READ_FILE_MAX_CHARS]
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
        return truncated + f"\n... (truncated at {READ_FILE_MAX_CHARS} characters; file has more content past this point, use start_line/end_line to read a narrower range)"
    return text


def _dispatch_tool(name: str, args: dict, root: Path) -> str:
    if name == "grep":
        return _tool_grep(root, args.get("pattern", ""), args.get("glob"))
    if name == "list_files":
        return _tool_list_files(root, args.get("glob", "*"))
    if name == "read_file":
        return _tool_read_file(root, args.get("path", ""), args.get("start_line"), args.get("end_line"))
    return f"(unknown tool: {name})"


def _default_agentic_chain(config: Config) -> list[Generator]:
    # timeout override: the agentic tool loop's conversation grows with
    # every tool result appended to messages (list_files/grep/read_file
    # output, see _trim_messages's cap of 60 total messages) — a
    # multi-thousand-token cumulative context is normal here in a way a
    # single Q&A chat completion never sees. Confirmed live: the DEFAULT
    # 60s Generator timeout (generate.py) genuinely wasn't enough once a
    # read_file result plus prior tool history pushed the request past
    # Groq's response time for this size of prompt — a plain httpx
    # ReadTimeout on BOTH the primary and NIM fallback generator, not a
    # 429/rate-limit at all. Doesn't replace READ_FILE_MAX_CHARS (which
    # bounds how large any single result can get) — this is headroom for
    # the cumulative case that one result cap alone can't fully prevent.
    agentic_timeout = 150
    groq_gen = GroqGenerator(api_key=config.groq_api_key, model_name=AGENTIC_GROQ_MODEL)
    groq_gen.timeout = agentic_timeout
    chain: list[Generator] = [groq_gen]
    if config.nim_api_key:
        nim_gen = NimGenerator(api_key=config.nim_api_key)
        nim_gen.timeout = agentic_timeout
        chain.append(nim_gen)
    return chain


async def _call(chain: list[Generator], messages: list[dict]) -> str:
    return "".join([t async for t in chat_with_fallback(chain, messages, stream=False)])


@dataclass
class AgentSession:
    """A live, multi-turn agentic session pinned to one local directory.

    Call ask() once per user question; messages persist across calls so a
    later question can refer back to what an earlier tool call found.
    """

    path: str
    config: Config | None = None
    generator: Generator | None = None
    fallback_chain: list[Generator] | None = None
    max_messages: int = DEFAULT_MAX_MESSAGES
    root: Path = field(init=False)
    messages: list[dict] = field(init=False)
    _chain: list[Generator] = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.path)
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Same override precedence as the old run_agentic(): an explicit
        # fallback_chain wins, then a single generator (back-compat/test
        # convenience, treated as a one-generator chain), then the real
        # default chain built from config. Never routed through
        # get_fallback_chain(config) — that helper pins config.groq_model,
        # the regular-Q&A model, not AGENTIC_GROQ_MODEL.
        if self.fallback_chain is not None:
            self._chain = self.fallback_chain
        elif self.generator is not None:
            self._chain = [self.generator]
        else:
            self._chain = _default_agentic_chain(self.config)

    def _trim_messages(self) -> None:
        # Keep the system prompt (index 0) always; once over the cap, drop
        # the oldest non-system message pair (an assistant tool-call plus
        # its following tool-result "user" message) rather than the oldest
        # single message, so the list never gets left with a dangling
        # orphaned tool-result with no corresponding call.
        while len(self.messages) > self.max_messages:
            del self.messages[1:3]

    def abort_turn(self) -> None:
        """Close out a turn that was cancelled part-way through.

        ask() appends to self.messages as it goes — the question, then each
        assistant tool-call and its tool result. If the turn is cancelled
        mid-flight (the REPL's Ctrl-C, see repl.py's run_turn_interruptibly),
        all of that stays in history with NO answer ever produced. The next
        question is then simply appended after it, so the model sees a
        still-pending investigation followed by a short new message — and
        reliably finishes answering the OLD question instead. Reported
        symptom: interrupt a slow question, ask something unrelated, get the
        previous question's answer back.

        The fix is to make the abandonment explicit in the transcript the
        model reads, rather than silently truncating history:

        - A dangling assistant tool-call whose result never arrived gets a
          synthetic result, because a tool_call with no result is malformed
          conversation shape and _trim_messages() drops messages in
          assistant/user PAIRS (del self.messages[1:3]) — an odd number of
          trailing messages would misalign every later pair.
        - A user-visible note tells the model the previous request was
          abandoned and must not be resumed.

        Deliberately NOT implemented by rolling history back to the
        pre-question state: the tool results gathered before the interrupt
        are real, often useful context (the user may well ask a follow-up
        about what was already found), and discarding them would also throw
        away the work the interrupt was meant to stop, not undo.
        """
        # Nothing to do if no turn is in progress or the last turn ended
        # cleanly (ask() always leaves an assistant answer as the last
        # message on a normal return).
        if len(self.messages) <= 1:
            return

        last_role = self.messages[-1]["role"]

        if last_role == "assistant":
            parsed = None
            try:
                parsed = _parse_tool_call(self.messages[-1]["content"])
            except MalformedToolCall:
                parsed = None
            if parsed is None:
                # A completed answer already closes the turn cleanly; there
                # is nothing abandoned to mark. Must stay a no-op, or a
                # stray call would tell the model to disown its own last
                # answer and it would refuse follow-up questions about it.
                return
            # Cancelled between yielding the tool call and appending its
            # result — pair the dangling call off with a synthetic result.
            self.messages.append(
                {"role": "user", "content": "Tool result: (interrupted by the user before this tool ran)"}
            )

        # Close the turn with an ASSISTANT message rather than another user
        # message. Two reasons, both load-bearing:
        #   1. Alternation. The last message at this point always has role
        #      "user" (the bare question if the interrupt landed during the
        #      first model call, a tool result otherwise). Appending another
        #      "user" note would put two consecutive user messages in
        #      history, and _trim_messages() deletes in pairs
        #      (del self.messages[1:3]) assuming assistant/user alternation
        #      — so a later trim would drop both and leave a dangling
        #      assistant message.
        #   2. It reads as the model's own prior statement rather than as an
        #      instruction buried in a user turn, which is markedly harder
        #      for it to ignore on the next call.
        self.messages.append(
            {
                "role": "assistant",
                "content": (
                    "(This request was interrupted by the user before I answered it. "
                    "It is abandoned — I will not continue or answer it. Anything found "
                    "above remains available as context, and I will respond only to the "
                    "user's next question.)"
                ),
            }
        )
        self._trim_messages()

    async def ask(self, question: str) -> AsyncIterator[AgentEvent]:
        self.messages.append({"role": "user", "content": question})

        for _ in range(MAX_ITERATIONS):
            try:
                response_text = await _call(self._chain, self.messages)
            except (httpx.HTTPStatusError, httpx.TransportError, RuntimeError):
                yield AnswerEvent(text=UNAVAILABLE_MESSAGE)
                return

            try:
                parsed = _parse_tool_call(response_text)
            except MalformedToolCall as exc:
                # This clearly LOOKED like an attempted tool call (matched
                # the TOOL: <name> {...} shape) but the JSON args didn't
                # parse — treating that as a genuine final answer would
                # show the user a raw broken "TOOL: grep {...}" line
                # instead of a real answer. Feed the parse error back the
                # same way a dispatch error is fed back, so the model gets
                # a chance to correct its own syntax on the next turn, and
                # count this as one of the MAX_ITERATIONS iterations so a
                # persistently broken model still terminates.
                self.messages.append({"role": "assistant", "content": response_text})
                error_result = f"(error: malformed TOOL call — args must be valid JSON: {exc.parse_error})"
                self.messages.append({"role": "user", "content": f"Tool result: {error_result}"})
                self._trim_messages()
                continue

            if parsed is None:
                self.messages.append({"role": "assistant", "content": response_text})
                yield AnswerEvent(text=response_text)
                self._trim_messages()
                return

            self.messages.append({"role": "assistant", "content": response_text})
            name, args = parsed
            yield ToolCallEvent(name=name, args=args)
            result = _dispatch_tool(name, args, self.root)
            yield ToolResultEvent(name=name, result=result)
            self.messages.append({"role": "user", "content": f"Tool result: {result}"})
            self._trim_messages()

        self.messages.append(
            {
                "role": "user",
                "content": "Iteration limit reached. Answer the original question with what "
                "you've gathered so far, as plain prose.",
            }
        )
        try:
            response_text = await _call(self._chain, self.messages)
        except (httpx.HTTPStatusError, httpx.TransportError, RuntimeError):
            yield AnswerEvent(text=UNAVAILABLE_MESSAGE)
            return
        self.messages.append({"role": "assistant", "content": response_text})
        yield AnswerEvent(text=response_text, truncated=True)
        self._trim_messages()
