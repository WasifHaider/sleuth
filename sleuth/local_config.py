"""Local, on-disk Groq API key storage for the standalone `sleuth-repl` package.

The distributed REPL-only package (see pyproject.toml / sleuth/repl_entry.py)
ships with ZERO embedded secrets — no Voyage key, no Supabase URL, no Groq
key. On first run it prompts the user for their own Groq API key exactly
once, then persists it to a local file (~/.sleuth/config.json by default)
so every later `sleuth` invocation on that machine reads it back instead of
prompting again.

Deliberately separate from sleuth/config.py's load_config(): that function
still backs the full monorepo CLI (ingest/eval/ask — Postgres+Voyage-backed,
env-var/.env driven) and is unrelated to this on-disk, prompt-once flow.
"""

import json
import os
import stat
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".sleuth"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"

# Sourcing GROQ_API_KEY from the environment first (before ever touching the
# saved file or prompting) lets a user override the saved key for one run
# without editing/deleting the persisted file — same override precedence
# CI/scripts expect from any env-var-driven tool.
ENV_OVERRIDE_VAR = "GROQ_API_KEY"


def _config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("SLEUTH_CONFIG_PATH")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def load_saved_groq_api_key(path: Path | None = None) -> str | None:
    """Reads a previously-saved key back from disk, or None if there isn't one.

    Never raises on a missing/corrupt file — a first run (file doesn't
    exist yet) and a hand-edited-into-garbage file are both just "nothing
    saved", handled the same way by falling through to the prompt.
    """
    resolved = _config_path(path)
    if not resolved.is_file():
        return None
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    key = data.get("groq_api_key")
    return key or None


def save_groq_api_key(api_key: str, path: Path | None = None) -> None:
    """Persists the key to disk, creating ~/.sleuth if needed.

    Chmod'd to owner-read/write-only (0600) where the platform supports it
    (POSIX) — best-effort on Windows, where chmod has no real effect; this
    is the same "can't do better than the OS' own file ACLs" situation
    every local secrets file (~/.aws/credentials, ~/.netrc, ...) is in.
    """
    resolved = _config_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps({"groq_api_key": api_key}), encoding="utf-8")
    try:
        os.chmod(resolved, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def resolve_groq_api_key(prompt=input, path: Path | None = None) -> str:
    """Returns a usable Groq API key: env var > saved file > interactive prompt.

    A freshly-entered key is saved immediately so this is genuinely a
    "asks once" flow, not "asks every run until you happen to also save it
    yourself". Re-prompts (rather than accepting an empty string) if the
    user just hits enter, since an empty key would silently fail every
    later Groq call with an unhelpful 401.
    """
    env_key = os.environ.get(ENV_OVERRIDE_VAR)
    if env_key:
        return env_key

    saved = load_saved_groq_api_key(path)
    if saved:
        return saved

    entered = prompt(
        "No Groq API key found. Get a free one at https://console.groq.com/keys\n"
        "Enter your Groq API key: "
    ).strip()
    while not entered:
        entered = prompt("A Groq API key is required to continue: ").strip()

    save_groq_api_key(entered, path)
    return entered
