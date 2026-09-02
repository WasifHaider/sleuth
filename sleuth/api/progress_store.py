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

# NOTE: this store is per-process. If the API is ever run with more than
# one uvicorn worker, a client's SSE connection can land on a worker that
# never ran the ingest for that repo_id, and get() returns None forever —
# fine on a single worker (the only deployment today), but worth flagging
# so a future multi-worker deploy doesn't turn this into a silent mystery.


def _evict_oldest_if_over_cap() -> None:
    while len(_progress) > _MAX_TRACKED_REPOS:
        _progress.popitem(last=False)


def start(repo_id: str) -> None:
    with _lock:
        _progress[repo_id] = {
            "step": "cloning", "detail": {}, "log": [],
            "started_at": time.monotonic(), "version": 0,
        }
        _progress.move_to_end(repo_id)
        _evict_oldest_if_over_cap()


def record(repo_id: str, step: str, **detail) -> None:
    with _lock:
        entry = _progress.setdefault(
            repo_id,
            {"step": step, "detail": {}, "log": [], "started_at": time.monotonic(), "version": 0},
        )
        entry["step"] = step
        entry["detail"] = detail
        entry["log"].append({"step": step, **detail})
        entry["log"] = entry["log"][-20:]
        # Bumped unconditionally on every call, independent of step/detail/log
        # — those can all repeat or get capped (log is trimmed to 20 entries
        # above), so none of them is a safe "did anything change?" signal on
        # their own. version can't repeat and can't be trimmed, so it's the
        # one thing the SSE stream (repos.py::stream_progress) can compare
        # to know a real update happened, no matter how many calls land in
        # between two polls.
        entry["version"] += 1
        _progress.move_to_end(repo_id)
        _evict_oldest_if_over_cap()


def get(repo_id: str) -> dict | None:
    with _lock:
        entry = _progress.get(repo_id)
        if entry is None:
            return None
        # Copy detail/log rather than handing back the live dict/list —
        # record() can run concurrently on the ingest pipeline's threadpool
        # thread while a caller (e.g. the SSE generator) is still
        # json.dumps()-ing what get() returned, which would otherwise be
        # mutating the same object mid-serialization.
        return {
            "step": entry["step"],
            "detail": dict(entry["detail"]),
            "log": list(entry["log"]),
            "elapsed_seconds": time.monotonic() - entry["started_at"],
            "version": entry["version"],
        }
