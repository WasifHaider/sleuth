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
    if glob:
        args += ["--glob", glob]
    args += [pattern, str(root)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, cwd=root)
    except FileNotFoundError:
        return "(error: ripgrep (rg) is not installed or not on PATH)"

    if proc.returncode == 2:
        # rg's own convention: exit 2 means a real error (bad regex, bad
        # glob, unreadable path, ...) — never raise on model-supplied input,
        # feed the error back as a tool result so the model can retry.
        return f"(invalid pattern or rg error: {proc.stderr.strip()})"
    if proc.returncode not in (0, 1):
        return f"(rg exited with code {proc.returncode}: {proc.stderr.strip()})"

    lines = [_relativize_rg_line(line, root) for line in proc.stdout.splitlines()]
    if not lines:
        return "(no matches)"
    if len(lines) > GREP_MAX_MATCHES:
        return "\n".join(lines[:GREP_MAX_MATCHES]) + "\n... (truncated at 50 matches)"
    return "\n".join(lines)


def _relativize_rg_line(line: str, root: Path) -> str:
    # rg's --no-heading output is "absolute/path:lineno:content" — reformat
    # the leading path relative to root to match the tool's documented
    # "path:lineno: line text" contract (and avoid leaking the host's
    # absolute filesystem layout into the model's context).
    parts = line.split(":", 2)
    if len(parts) != 3:
        return line
    path_str, lineno, content = parts
    try:
        rel = Path(path_str).relative_to(root)
    except ValueError:
        rel = Path(path_str)
    return f"{rel}:{lineno}: {content.strip()}"


def _tool_list_files(root: Path, glob: str) -> str:
    args = ["rg", "--files", "--no-require-git", "--glob", glob]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, cwd=root)
    except FileNotFoundError:
        return "(error: ripgrep (rg) is not installed or not on PATH)"

    if proc.returncode not in (0, 1):
        return f"(rg exited with code {proc.returncode}: {proc.stderr.strip()})"

    paths = sorted(proc.stdout.splitlines())
    if not paths:
        return "(no files matched)"
    if len(paths) > LIST_FILES_MAX_RESULTS:
        return "\n".join(paths[:LIST_FILES_MAX_RESULTS]) + "\n... (truncated at 200 files)"
    return "\n".join(paths)


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
        proc = subprocess.run(
            ["sed", "-n", f"{start},{end}p", str(target)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "(error: sed is not installed or not on PATH)"
    if proc.returncode != 0:
        return f"(error reading {path}: {proc.stderr.strip()})"

    lines = proc.stdout.splitlines()
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))


def _dispatch_tool(name: str, args: dict, root: Path) -> str:
    if name == "grep":
        return _tool_grep(root, args.get("pattern", ""), args.get("glob"))
    if name == "list_files":
        return _tool_list_files(root, args.get("glob", "*"))
    if name == "read_file":
        return _tool_read_file(root, args.get("path", ""), args.get("start_line"), args.get("end_line"))
    return f"(unknown tool: {name})"


def _default_agentic_chain(config: Config) -> list[Generator]:
    chain: list[Generator] = [GroqGenerator(api_key=config.groq_api_key, model_name=AGENTIC_GROQ_MODEL)]
    if config.nim_api_key:
        chain.append(NimGenerator(api_key=config.nim_api_key))
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
