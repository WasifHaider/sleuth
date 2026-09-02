import shutil
import subprocess
import time
from pathlib import Path


class CloneError(Exception):
    pass


# Substrings of known-transient git-clone failures: DNS/socket-thread hiccups
# and momentary connection drops, not "repo genuinely doesn't exist/access
# denied". Confirmed live: git-for-windows can fail a clone with
# "getaddrinfo() thread failed to start" (a WinSock thread-pool exhaustion
# bug, see git-for-windows/git#2495) that succeeds immediately on retry —
# clone_repo previously had zero retry, so one blip failed the whole ingest.
TRANSIENT_CLONE_ERROR_PATTERNS = (
    "getaddrinfo",
    "could not resolve host",
    "connection timed out",
    "connection refused",
    "connection reset",
    "recv failure",
    "empty reply from server",
)


def clone_repo(url: str, dest_dir: str, retries: int = 2, backoff_seconds: float = 2.0) -> Path:
    dest = Path(dest_dir)
    attempt = 0
    while True:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True,
            # Pinned UTF-8 rather than text=True's locale default (cp1252 on
            # native Windows Python): git writes UTF-8 diagnostics, and this
            # stderr is both pattern-matched below and stored verbatim in
            # repos.error_message for the UI. A locale decode failure here
            # raises on subprocess's background reader thread, where it
            # cannot be caught, so a clone failure would surface as an
            # unrelated crash instead of a recorded error.
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return dest

        stderr = result.stderr.strip()
        is_transient = any(pattern in stderr.lower() for pattern in TRANSIENT_CLONE_ERROR_PATTERNS)
        if is_transient and attempt < retries:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            time.sleep(backoff_seconds)
            attempt += 1
            continue

        raise CloneError(stderr)


def list_source_files(repo_path: Path, extensions: set[str]) -> list[Path]:
    files = []
    for path in Path(repo_path).rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_file() and path.suffix in extensions:
            files.append(path)
    return files
