import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from sleuth.config import Config
from sleuth.llm.generate import get_generator

MAX_ITERATIONS = 6
GREP_MAX_MATCHES = 50
READ_FILE_MAX_LINES = 400

SYSTEM_PROMPT = (
    "You are a code assistant investigating a local codebase to answer the user's "
    "question. You have three tools:\n"
    "  grep(pattern, glob=null) -- regex search across files, first 50 matches\n"
    "  list_files(glob) -- list files matching a glob\n"
    "  read_file(path, start_line=null, end_line=null) -- read up to 400 lines of a file\n"
    "To call a tool, respond with EXACTLY one line in this form and nothing else:\n"
    'TOOL: <tool_name> {"arg": "value", ...}\n'
    "When you have enough information to answer, respond with your final answer as "
    "plain prose (no TOOL: prefix)."
)

_TOOL_LINE_RE = re.compile(r"^TOOL:\s*(\w+)\s*(\{.*\})\s*$", re.DOTALL)


def _parse_tool_call(text: str) -> tuple[str, dict] | None:
    match = _TOOL_LINE_RE.match(text.strip())
    if not match:
        return None
    try:
        args = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    return match.group(1), args


def _tool_grep(root: Path, pattern: str, glob: str | None = None) -> str:
    regex = re.compile(pattern)
    matches = []
    paths = sorted(root.rglob(glob or "*"))
    for path in paths:
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
                if len(matches) >= GREP_MAX_MATCHES:
                    return "\n".join(matches) + "\n... (truncated at 50 matches)"
    return "\n".join(matches) if matches else "(no matches)"


def _tool_list_files(root: Path, glob: str) -> str:
    paths = sorted(p for p in root.rglob(glob) if p.is_file() and ".git" not in p.parts)
    return "\n".join(str(p.relative_to(root)) for p in paths) or "(no files matched)"


def _tool_read_file(root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    target = root / path
    try:
        lines = target.read_text(errors="ignore").splitlines()
    except OSError as exc:
        return f"(error reading {path}: {exc})"

    start = max((start_line or 1) - 1, 0)
    end = min(end_line or len(lines), start + READ_FILE_MAX_LINES, len(lines))
    snippet = lines[start:end]
    return "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(snippet))


def _dispatch_tool(name: str, args: dict, root: Path) -> str:
    if name == "grep":
        return _tool_grep(root, args.get("pattern", ""), args.get("glob"))
    if name == "list_files":
        return _tool_list_files(root, args.get("glob", "*"))
    if name == "read_file":
        return _tool_read_file(root, args.get("path", ""), args.get("start_line"), args.get("end_line"))
    return f"(unknown tool: {name})"


async def _call(generator, messages: list[dict]) -> str:
    return "".join([t async for t in generator.chat(messages, stream=False)])


async def run_agentic(question: str, path: str, config: Config, generator=None) -> AsyncIterator[str]:
    generator = generator or get_generator(config)
    root = Path(path)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_ITERATIONS):
        response_text = await _call(generator, messages)
        parsed = _parse_tool_call(response_text)

        if parsed is None:
            yield response_text
            return

        messages.append({"role": "assistant", "content": response_text})
        name, args = parsed
        result = _dispatch_tool(name, args, root)
        messages.append({"role": "user", "content": f"Tool result: {result}"})

    messages.append(
        {
            "role": "user",
            "content": "Iteration limit reached. Answer the original question with what "
            "you've gathered so far, as plain prose.",
        }
    )
    response_text = await _call(generator, messages)
    yield response_text + "\n\n(Note: search was cut short after reaching the iteration limit.)"
