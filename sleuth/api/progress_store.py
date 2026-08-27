import time
from threading import Lock

_progress: dict[str, dict] = {}
_lock = Lock()


def start(repo_id: str) -> None:
    with _lock:
        _progress[repo_id] = {"step": "cloning", "detail": {}, "log": [], "started_at": time.monotonic()}


def record(repo_id: str, step: str, **detail) -> None:
    with _lock:
        entry = _progress.setdefault(
            repo_id, {"step": step, "detail": {}, "log": [], "started_at": time.monotonic()}
        )
        entry["step"] = step
        entry["detail"] = detail
        entry["log"].append({"step": step, **detail})
        entry["log"] = entry["log"][-20:]


def get(repo_id: str) -> dict | None:
    with _lock:
        entry = _progress.get(repo_id)
        if entry is None:
            return None
        return {
            "step": entry["step"],
            "detail": entry["detail"],
            "log": entry["log"],
            "elapsed_seconds": time.monotonic() - entry["started_at"],
        }
