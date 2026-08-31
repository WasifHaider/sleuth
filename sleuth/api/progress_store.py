import time
from collections import OrderedDict
from threading import Lock

# Unbounded before this — every repo ever ingested left a permanent entry
# for the life of the process (each one small, but with no eviction at all
# a long-running server ingesting many repos over time leaks memory
# forever). Capped as a simple insertion-ordered LRU: oldest entries are
# evicted once the cap is hit, on the assumption nobody's still polling
# progress for a repo indexed hundreds of repos ago.
_MAX_TRACKED_REPOS = 500
_progress: "OrderedDict[str, dict]" = OrderedDict()
_lock = Lock()


def _evict_oldest_if_over_cap() -> None:
    while len(_progress) > _MAX_TRACKED_REPOS:
        _progress.popitem(last=False)


def start(repo_id: str) -> None:
    with _lock:
        _progress[repo_id] = {"step": "cloning", "detail": {}, "log": [], "started_at": time.monotonic()}
        _progress.move_to_end(repo_id)
        _evict_oldest_if_over_cap()


def record(repo_id: str, step: str, **detail) -> None:
    with _lock:
        entry = _progress.setdefault(
            repo_id, {"step": step, "detail": {}, "log": [], "started_at": time.monotonic()}
        )
        entry["step"] = step
        entry["detail"] = detail
        entry["log"].append({"step": step, **detail})
        entry["log"] = entry["log"][-20:]
        _progress.move_to_end(repo_id)
        _evict_oldest_if_over_cap()


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
