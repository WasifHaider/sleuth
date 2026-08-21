# RAG Web App (FastAPI + React) Implementation Plan — Plan 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` "Execution mode" section takes precedence over the sub-skill's default flow — work one full task at a time (not step-by-step with per-step confirmation), explain the concept after each task, log it to `docs/progress.html`, and wait for the user's "okay" before starting the next task. Git commits are done by the user, not Claude — the "Commit" step in each task below documents what *would* be committed; do not run it yourself unless asked.

**Goal:** Expose the existing pipeline (ingest/retrieve/eval, all done in Plan 1)
through a FastAPI backend and a React (Vite) app implementing the five-screen
design already built in Claude Design (landing page + Repos/Indexing/Chat/Eval
app screens) — add repo by URL, watch indexing progress live, chat against a
ready repo with streamed answers and persisted history, review eval results.

**Design source:** Claude Design project `SLEUTH chatbot design direction`
(project id `bbe0f14c-4656-4fc8-9f02-dee2c0bdb312`), files `Sleuth.dc.html`,
`Repos.dc.html`, `Indexing.dc.html`, `Chat.dc.html`, `Eval.dc.html`,
`support.js`. Pulled in full via the `claude_design` MCP on 2026-08-19; exact
palette/typography/copy/layout below is transcribed from those files, not
reconstructed from memory.

**Design doc:** `docs/superpowers/specs/2026-08-13-rag-code-chatbot-design-v2.md`
(`api/main.py`, `web/` sections; Non-Goals: no prod hosting, no auth/multi-user).

**Architecture:** `sleuth/api/` is a FastAPI app calling the same `sleuth/`
modules the CLI already calls (`store.py`, `ingest/pipeline.py`,
`retrieve/answer.py`, `eval/runner.py`) — no logic duplicated. Indexing runs
as a FastAPI `BackgroundTasks` job (same process, no queue/worker infra).
Chat answers stream to the browser via Server-Sent Events (SSE), reusing the
existing token-generator from `retrieve/answer.py::stream_answer`. Three
existing pipeline functions (`ingest_repo`, `VoyageEmbedder.embed_batch`,
`stream_answer`) get one small additive change each: an optional callback
parameter (default `None`, fully backward compatible with every existing call
site and test) so the API layer can observe progress/sources without
duplicating any pipeline logic. React talks to the API over plain `fetch` +
`react-router-dom` for the five screens — no Redux/React Query, no
TypeScript, kept simple since this is the user's first React project.

**Tech stack additions:** FastAPI, uvicorn, `httpx`'s `TestClient`
(`fastapi.testclient`) for backend tests. React 18 + Vite, `react-router-dom`
(one added dependency, for the five URL-addressable screens), plain `fetch` +
`ReadableStream` for SSE consumption (`EventSource` can't send a POST body).
IBM Plex Mono (Google Fonts) for labels/data/code, system sans-serif for
prose — both already specified by the design files, loaded via a `<link>` in
`index.html`.

## Design System (transcribed from the `.dc.html` files)

All values are exact `oklch()` strings taken from the design files' inline
`<style>` blocks — implemented as CSS custom properties in
`web/src/theme.css` (Task 6):

```css
--bg: oklch(0.17 0.014 250);              /* page background */
--panel: oklch(0.14 0.012 250);           /* cards, sidebar, icon rail, nav */
--panel-alt: oklch(0.155 0.012 250);      /* browser-chrome header strips */
--text: oklch(0.95 0.006 250);            /* primary text */
--text-secondary: oklch(0.75 0.01 250);
--text-muted: oklch(0.6 0.012 250);
--text-faint: oklch(0.55 0.01 250);
--border: oklch(1 0 0 / 0.07);
--border-strong: oklch(1 0 0 / 0.14);
--accent: oklch(0.62 0.10 148);           /* brand green, ~#4FA377 */
--accent-hover: oklch(0.68 0.10 148);
--accent-wash: oklch(0.62 0.10 148 / 0.14);
--accent-on: oklch(0.15 0.02 148);        /* text on accent-filled buttons */
--status-ready: oklch(0.75 0.16 150);     /* separate, warmer/brighter semantic green */
--status-ready-wash: oklch(0.72 0.16 150 / 0.14);
--status-neutral: oklch(0.65 0.01 250);   /* failed/inactive pills */
--status-neutral-wash: oklch(0.6 0.01 250 / 0.14);
--compare-secondary: oklch(0.72 0.19 275); /* violet — second embedding-model bar in Eval */
--font-mono: 'IBM Plex Mono', monospace;
--font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
```

Dark-only (the design commits to one look; no light-mode variant exists in
the source files). Icon rail is a fixed 72px column, shared verbatim across
Repos/Indexing/Chat/Eval (logo link to `/`, then Repos/Indexing/Chat/Eval
icons, active route gets `background: var(--accent-wash)` on a 40×40 9px
rounded box).

The Chat screen's "Accent Color" swatch picker (`data-props` on the `.dc.html`
file) is a Claude Design authoring control, not a shipped feature — per user
decision 2026-08-19, the real app always uses `--accent` above, no in-app
color picker.

## Global Constraints

- No new pipeline *business* logic. `sleuth/api/` only calls existing
  `store.py`, `ingest/pipeline.py::ingest_repo`, `retrieve/answer.py::stream_answer`,
  `eval/runner.py::run_eval`. The three optional instrumentation callbacks
  added in Tasks 2/4/5 are additive (default `None`, no behavior change for
  any existing caller) — not new pipeline logic, just observability hooks.
- Indexing progress is kept in an in-process dict (`sleuth/api/progress_store.py`),
  not persisted — resets on backend restart. Acceptable for a local dev tool
  (per design doc Non-Goals: no prod hosting); avoids a queue/worker or a new
  DB table for something that's inherently transient.
- Chat history **is** persisted (`chats`/`messages` tables, Task 3) — decided
  2026-08-19 in favor of surviving page reload, over the simpler ephemeral
  client-state option.
- Eval runs are persisted (`eval_runs` table, Task 5) so the Eval screen shows
  real history, not fixture data.
- The Eval screen's provider-comparison bar chart renders one bar per
  embedding model that has completed eval runs for the repo. Today that's
  Voyage only (Plan 1 scope note: no NIM embedder was built) — so it renders
  a single bar per metric. The component takes a list, not two fixed slots,
  so a second (violet) bar appears automatically if NIM embedding is ever
  added — no rework needed later. Decided 2026-08-19.
- Indexing screen shows **elapsed time**, not the design mockup's fabricated
  "ETA" — there's no reliable way to estimate remaining time from current
  pipeline signals, and inventing one would just be a fake number with a
  precise-looking label. Decided 2026-08-19.
- The `hit-rate@5` label in `Eval.dc.html` is a design placeholder; the real
  eval harness uses `TOP_K = 8` (`sleuth/eval/runner.py`). The Eval screen
  must read the real top-k value from the eval run's stored result, not
  hardcode `@5`.
- A chat request against a repo whose `status != 'ready'` is rejected with a
  clear 409, never run against a partial/absent index.
- CORS enabled for the Vite dev server origin only (`http://localhost:5173`).
- No auth — single-user local app.
- No global state library (Redux/Zustand/React Query) and no TypeScript —
  plain `useState`/`useEffect` + `fetch`. `react-router-dom` is the one
  added frontend dependency (routing, not state management).
- Every backend endpoint gets a test using FastAPI's `TestClient` against the
  real test Postgres (existing `tests/conftest.py::pg_conn` fixture), not
  mocked DB calls. External HTTP (Voyage/Groq) is mocked at the transport
  level with `respx`, exactly as `tests/test_answer.py` and
  `tests/test_eval_runner.py` already do — not by mocking `Generator`/`Embedder`
  objects.

---

## File Structure (additions)

```
sleuth/
  api/
    __init__.py
    main.py                # FastAPI() app, CORS, router includes
    schemas.py              # Pydantic request/response models
    progress_store.py       # in-memory per-repo indexing progress
    routes/
      __init__.py
      repos.py               # POST/GET /repos, GET /repos/{id}, GET /repos/{id}/progress
      chat.py                 # POST /chats, GET /chats, GET /chats/{id}/messages, POST /chat (SSE)
      eval.py                 # POST /eval, GET /eval/{id}, GET /eval
sleuth/ingest/pipeline.py   # modify: ingest_repo(..., on_event=None)
sleuth/ingest/embed.py      # modify: embed_batch(..., on_batch_done=None)
sleuth/retrieve/answer.py   # modify: stream_answer(..., on_sources=None)
sleuth/eval/runner.py       # modify: run_eval returns EvalSummary; format_table(summary) added
sleuth/store.py             # add: get_repo, chat/message CRUD, eval_run CRUD
sleuth/cli.py                # update eval command for EvalSummary
schema.sql                   # add: chats, messages, eval_runs tables
requirements.txt             # + fastapi, uvicorn[standard]
tests/
  test_api_repos.py
  test_api_chat.py
  test_api_eval.py
  (test_pipeline.py, test_embed.py, test_answer.py, test_eval_runner.py — extended, not replaced)

web/                      # new Vite React project
  package.json
  vite.config.js
  index.html
  .env.example             # VITE_API_URL
  src/
    main.jsx
    App.jsx                 # react-router-dom routes
    theme.css                # design tokens (see Design System above)
    api.js                    # fetch wrappers
    components/
      NavRail.jsx
      AppShell.jsx             # icon rail + <Outlet/>, shared by Repos/Indexing/Chat/Eval
      LandingPage.jsx
      RepoList.jsx
      AddRepoForm.jsx
      RepoStatusBadge.jsx
      IndexingScreen.jsx
      ChatScreen.jsx
      ChatSidebar.jsx
      MessageList.jsx
      Composer.jsx
      EvalScreen.jsx
      EvalBarChart.jsx
```

---

## Task 1: Store helper + FastAPI scaffolding + repo endpoints

**Files:**
- Modify: `sleuth/store.py` (add `get_repo`)
- Create: `sleuth/api/__init__.py`, `sleuth/api/main.py`, `sleuth/api/schemas.py`, `sleuth/api/routes/__init__.py`, `sleuth/api/routes/repos.py`
- Update: `requirements.txt` (+ `fastapi`, `uvicorn[standard]`)
- Test: `tests/test_api_repos.py`

**Interfaces:**
- Consumes: `sleuth.store.create_repo(conn, github_url) -> str`, `sleuth.store.list_repos(conn) -> list[tuple[str,str,str]]`, `sleuth.ingest.pipeline.ingest_repo(github_url, conn, config) -> str`, `sleuth.config.load_config() -> Config`, `sleuth.db.get_connection(url)`, `sleuth.db.apply_schema(conn)`.
- Produces: `sleuth.store.get_repo(conn, repo_id) -> dict | None` with keys `id, github_url, status, error_message, embedding_model, embedding_dim` — used by every later task that needs a single repo's status. `RepoOut` Pydantic schema with the same fields, used by Tasks 1-5. `POST /repos`, `GET /repos`, `GET /repos/{id}` routes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_repos.py
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from tests.conftest import TEST_DATABASE_URL


def _client():
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url=TEST_DATABASE_URL)
    return TestClient(create_app(config))


def test_get_unknown_repo_returns_404(pg_conn):
    resp = _client().get("/repos/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@respx.mock
def test_add_list_get_repo_round_trip(pg_conn):
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _client()

    resp = client.post("/repos", json={"github_url": "https://github.com/example/repo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["github_url"] == "https://github.com/example/repo"
    assert body["status"] == "pending"
    repo_id = body["id"]

    listed = client.get("/repos").json()
    assert any(r["id"] == repo_id for r in listed)

    for _ in range(50):
        got = client.get(f"/repos/{repo_id}").json()
        if got["status"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert got["status"] in ("ready", "failed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_api_repos.py -v`
Expected: FAIL — `sleuth.api` doesn't exist yet.

- [ ] **Step 3: Add `get_repo` to `sleuth/store.py`**

```python
def get_repo(conn: psycopg.Connection, repo_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
        "FROM repos WHERE id = %s",
        (repo_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "github_url": row[1],
        "status": row[2],
        "error_message": row[3],
        "embedding_model": row[4],
        "embedding_dim": row[5],
    }
```

- [ ] **Step 4: Write `sleuth/api/schemas.py`**

```python
from pydantic import BaseModel


class RepoOut(BaseModel):
    id: str
    github_url: str
    status: str
    error_message: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None


class AddRepoIn(BaseModel):
    github_url: str
```

- [ ] **Step 5: Write `sleuth/api/routes/repos.py`**

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from sleuth.api.schemas import AddRepoIn, RepoOut
from sleuth.db import get_connection
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import create_repo, get_repo, list_repos

router = APIRouter()


async def _run_ingest(github_url: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    try:
        await ingest_repo(github_url, conn, config)
    finally:
        conn.close()


@router.post("/repos", response_model=RepoOut)
def add_repo(body: AddRepoIn, request: Request, background_tasks: BackgroundTasks) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url)
    conn.commit()
    background_tasks.add_task(_run_ingest, body.github_url, config.database_url, config)
    return RepoOut(**get_repo(conn, repo_id))


@router.get("/repos", response_model=list[RepoOut])
def get_repos(request: Request) -> list[RepoOut]:
    conn = request.state.conn
    return [
        RepoOut(**get_repo(conn, repo_id))
        for repo_id, _github_url, _status in list_repos(conn)
    ]


@router.get("/repos/{repo_id}", response_model=RepoOut)
def get_repo_by_id(repo_id: str, request: Request) -> RepoOut:
    repo = get_repo(request.state.conn, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return RepoOut(**repo)
```

- [ ] **Step 6: Write `sleuth/api/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sleuth.api.routes import repos
from sleuth.config import Config, load_config
from sleuth.db import apply_schema, get_connection


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="Sleuth API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_conn(request, call_next):
        conn = get_connection(config.database_url)
        apply_schema(conn)
        request.state.conn = conn
        request.state.config = config
        try:
            return await call_next(request)
        finally:
            conn.close()

    app.include_router(repos.router)
    return app


app = create_app()
```

- [ ] **Step 7: Run to verify it passes**

Run: `pytest tests/test_api_repos.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add sleuth/store.py sleuth/api requirements.txt tests/test_api_repos.py
git commit -m "feat: add FastAPI scaffolding and repo endpoints"
```

---

## Task 2: Indexing progress instrumentation + `GET /repos/{id}/progress`

**Files:**
- Modify: `sleuth/ingest/embed.py` (`VoyageEmbedder.embed_batch` gains `on_batch_done`)
- Modify: `sleuth/ingest/pipeline.py` (`ingest_repo` gains `on_event`)
- Create: `sleuth/api/progress_store.py`
- Modify: `sleuth/api/routes/repos.py` (wire `on_event` into the background task, add `GET /repos/{id}/progress`)
- Test: extend `tests/test_embed.py`, `tests/test_pipeline.py`; create `tests/test_api_repos.py::test_progress_endpoint...` (append to existing file)

**Interfaces:**
- Consumes: Task 1's `RepoOut`/routing setup.
- Produces: `progress_store.start(repo_id)`, `progress_store.record(repo_id, step, **detail)`, `progress_store.get(repo_id) -> dict | None` (keys `step, detail, log, elapsed_seconds`) — consumed by Task 9 (Indexing screen). `embed_batch(texts, on_batch_done: Callable[[int, int], None] | None = None)`. `ingest_repo(github_url, conn, config, on_event: Callable[[str, dict], None] | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_embed.py
@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_reports_progress_via_callback():
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    embedder = VoyageEmbedder(api_key="k", batch_size=1)
    calls = []
    await embedder.embed_batch(["a", "b"], on_batch_done=lambda done, total: calls.append((done, total)))

    assert len(calls) == 2
    assert all(total == 2 for _done, total in calls)
    assert {done for done, _total in calls} == {1, 2}
```

```python
# append to tests/test_pipeline.py
@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_emits_progress_events(pg_conn, tmp_git_repo):
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    events = []
    await ingest_repo(str(tmp_git_repo), pg_conn, config, on_event=lambda step, detail: events.append((step, detail)))

    steps = [step for step, _detail in events]
    assert "cloned" in steps
    assert "ready" in steps
```

Use whatever local-repo fixture `tests/test_pipeline.py` already defines for clone-from-disk (check the existing file for the fixture name — reuse it rather than adding a second one).

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_embed.py tests/test_pipeline.py -v -k progress`
Expected: FAIL — `on_batch_done`/`on_event` not accepted yet.

- [ ] **Step 3: Add `on_batch_done` to `embed_batch`**

```python
async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
    batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
    semaphore = asyncio.Semaphore(self.max_concurrency)
    total = len(batches)
    completed = 0

    async def run_batch(batch):
        nonlocal completed
        async with semaphore:
            vectors = await self._embed_one_batch_impl(batch)
        completed += 1
        if on_batch_done:
            on_batch_done(completed, total)
        return vectors

    async with httpx.AsyncClient() as client:
        self._client = client
        results = await asyncio.gather(*(self._run_batch_with_client(client, semaphore, batch, on_batch_done, total) for batch in batches))

    vectors: list[list[float]] = []
    for batch_vectors in results:
        vectors.extend(batch_vectors)
    return vectors
```

This needs the semaphore-guarded HTTP call kept inside the client context, so restructure `_embed_one_batch` into a version that also fires the callback on completion, without changing its request/response handling:

```python
    async def _run_batch_with_client(self, client, semaphore, batch, on_batch_done, total):
        async with semaphore:
            vectors = await self._embed_one_batch(client, semaphore, batch)
        if on_batch_done:
            on_batch_done(getattr(self, "_completed", 0) + 1, total)
        return vectors
```

Simplify: track `completed` via a mutable counter shared across the gathered coroutines (a single-element list, since Python closures can't rebind an outer int without `nonlocal`, and `nonlocal` works fine here since there's no nested `async def` inside another `async def` beyond one level):

```python
    async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)
        total = len(batches)
        completed = 0

        async def run_one(client, batch):
            nonlocal completed
            vectors = await self._embed_one_batch(client, semaphore, batch)
            completed += 1
            if on_batch_done:
                on_batch_done(completed, total)
            return vectors

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(run_one(client, batch) for batch in batches))

        vectors: list[list[float]] = []
        for batch_vectors in results:
            vectors.extend(batch_vectors)
        return vectors
```

`_embed_one_batch` is unchanged — it still acquires `semaphore` internally, so concurrency is still bounded by `max_concurrency`. `completed` incrementing outside the semaphore-guarded section is fine since `asyncio` coroutines are single-threaded; no lock needed.

- [ ] **Step 4: Write `sleuth/api/progress_store.py`**

```python
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
```

- [ ] **Step 5: Add `on_event` to `ingest_repo`**

```python
async def ingest_repo(github_url: str, conn, config: Config, on_event=None) -> str:
    def emit(step: str, **detail) -> None:
        if on_event:
            on_event(step, detail)

    repo_id = _find_or_create_repo(conn, github_url)
    update_repo_status(conn, repo_id, "indexing")
    conn.commit()
    emit("cloning")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)

    workdir = tempfile.mkdtemp(prefix="sleuth-clone-")
    try:
        try:
            repo_path = clone_repo(github_url, workdir)
        except CloneError as exc:
            update_repo_status(conn, repo_id, "failed", str(exc))
            conn.commit()
            emit("failed", error=str(exc))
            return repo_id

        files = list_source_files(repo_path, SUPPORTED_EXTENSIONS)
        emit("cloned", files=len(files))

        all_chunks = []
        skipped = 0
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                skipped += 1
                continue
            all_chunks.extend(chunks)
        emit("parsed", parsed=len(files) - skipped, skipped=skipped)
        emit("chunked", chunks=len(all_chunks))

        current_keys = {(c.file_path, c.symbol_name) for c in all_chunks}
        existing_hashes = get_existing_hashes(conn, repo_id)

        to_embed = [
            c for c in all_chunks
            if existing_hashes.get((c.file_path, c.symbol_name)) != c.content_hash
        ]

        if to_embed:
            texts = [
                format_chunk_context(c, EXTENSION_TO_LANGUAGE.get("." + c.file_path.rsplit(".", 1)[-1], ""))
                for c in to_embed
            ]
            emit("embedding_start", to_embed=len(to_embed))
            vectors = await embedder.embed_batch(
                texts,
                on_batch_done=lambda done, total: emit("embedding_progress", done=done, total=total),
            )
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)))
            conn.commit()

        set_repo_embedding_info(conn, repo_id, embedder.model_name, embedder.dim)
        conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys)
        conn.commit()
        emit("stored", upserted=len(to_embed), skipped_unchanged=len(all_chunks) - len(to_embed))

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        emit("ready")
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 6: Wire progress into the API route and add the endpoint**

In `sleuth/api/routes/repos.py`:

```python
from sleuth.api import progress_store


async def _run_ingest(repo_id: str, github_url: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    progress_store.start(repo_id)
    try:
        await ingest_repo(
            github_url, conn, config,
            on_event=lambda step, detail: progress_store.record(repo_id, step, **detail),
        )
    finally:
        conn.close()


@router.post("/repos", response_model=RepoOut)
def add_repo(body: AddRepoIn, request: Request, background_tasks: BackgroundTasks) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url)
    conn.commit()
    background_tasks.add_task(_run_ingest, repo_id, body.github_url, config.database_url, config)
    return RepoOut(**get_repo(conn, repo_id))


@router.get("/repos/{repo_id}/progress")
def get_progress(repo_id: str, request: Request) -> dict:
    repo = get_repo(request.state.conn, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    progress = progress_store.get(repo_id)
    if progress is None:
        return {"step": repo["status"], "detail": {}, "log": [], "elapsed_seconds": 0}
    return progress
```

- [ ] **Step 7: Run to verify it passes**

Run: `pytest tests/test_embed.py tests/test_pipeline.py tests/test_api_repos.py -v`
Expected: PASS, all existing tests in those files still green (no signature broke — every new parameter defaults to `None`).

- [ ] **Step 8: Commit**

```bash
git add sleuth/ingest/embed.py sleuth/ingest/pipeline.py sleuth/api/progress_store.py sleuth/api/routes/repos.py tests/test_embed.py tests/test_pipeline.py
git commit -m "feat: add indexing progress instrumentation and endpoint"
```

---

## Task 3: Chat persistence schema + chat CRUD endpoints

**Files:**
- Modify: `schema.sql` (add `chats`, `messages` tables)
- Modify: `sleuth/store.py` (add chat/message CRUD)
- Modify: `sleuth/api/schemas.py` (add `ChatOut`, `MessageOut`, `CreateChatIn`)
- Create: `sleuth/api/routes/chat.py` (CRUD part only — SSE endpoint is Task 4)
- Modify: `sleuth/api/main.py` (include the new router)
- Test: `tests/test_api_chat.py`

**Interfaces:**
- Consumes: Task 1's `get_repo`, `RepoOut` pattern.
- Produces: `store.create_chat(conn, repo_id, title="New chat") -> str`, `store.list_chats(conn, repo_id) -> list[dict]` (keys `id, title, created_at, message_count`), `store.get_chat(conn, chat_id) -> dict | None` (keys `id, repo_id, title`), `store.create_message(conn, chat_id, role, content, sources=None) -> str`, `store.list_messages(conn, chat_id) -> list[dict]` (keys `id, role, content, sources, created_at`). Routes `POST /chats`, `GET /chats`, `GET /chats/{id}/messages` — consumed by Task 4 (SSE endpoint reuses `create_message`) and Task 10 (Chat screen).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_chat.py
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from sleuth.store import create_repo, update_repo_status
from tests.conftest import TEST_DATABASE_URL


def _client():
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url=TEST_DATABASE_URL)
    return TestClient(create_app(config))


def test_create_chat_requires_ready_repo(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()  # status defaults to pending
    resp = _client().post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 409


def test_create_list_chat_and_messages_round_trip(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()

    client = _client()
    created = client.post("/chats", json={"repo_id": repo_id}).json()
    assert created["title"] == "New chat"

    listed = client.get(f"/chats?repo_id={repo_id}").json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["message_count"] == 0

    messages = client.get(f"/chats/{created['id']}/messages").json()
    assert messages == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_api_chat.py -v`
Expected: FAIL — `/chats` route doesn't exist.

- [ ] **Step 3: Add schema tables**

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS chats (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id    uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    title      text NOT NULL DEFAULT 'New chat',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id    uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,
    sources    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_chat_idx ON messages (chat_id, created_at);
```

- [ ] **Step 4: Add CRUD to `sleuth/store.py`**

```python
import json


def create_chat(conn: psycopg.Connection, repo_id: str, title: str = "New chat") -> str:
    row = conn.execute(
        "INSERT INTO chats (repo_id, title) VALUES (%s, %s) RETURNING id", (repo_id, title)
    ).fetchone()
    return str(row[0])


def list_chats(conn: psycopg.Connection, repo_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.created_at, COUNT(m.id)
        FROM chats c LEFT JOIN messages m ON m.chat_id = c.id
        WHERE c.repo_id = %s
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """,
        (repo_id,),
    ).fetchall()
    return [
        {"id": str(cid), "title": title, "created_at": created_at.isoformat(), "message_count": count}
        for cid, title, created_at, count in rows
    ]


def get_chat(conn: psycopg.Connection, chat_id: str) -> dict | None:
    row = conn.execute("SELECT id, repo_id, title FROM chats WHERE id = %s", (chat_id,)).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "repo_id": str(row[1]), "title": row[2]}


def create_message(
    conn: psycopg.Connection, chat_id: str, role: str, content: str, sources: list[dict] | None = None
) -> str:
    row = conn.execute(
        "INSERT INTO messages (chat_id, role, content, sources) VALUES (%s, %s, %s, %s) RETURNING id",
        (chat_id, role, content, json.dumps(sources) if sources is not None else None),
    ).fetchone()
    return str(row[0])


def list_messages(conn: psycopg.Connection, chat_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, sources, created_at FROM messages WHERE chat_id = %s ORDER BY created_at",
        (chat_id,),
    ).fetchall()
    return [
        {"id": str(mid), "role": role, "content": content, "sources": sources, "created_at": created_at.isoformat()}
        for mid, role, content, sources, created_at in rows
    ]
```

- [ ] **Step 5: Add schemas**

Append to `sleuth/api/schemas.py`:

```python
class CreateChatIn(BaseModel):
    repo_id: str


class ChatOut(BaseModel):
    id: str
    title: str
    created_at: str
    message_count: int = 0


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None = None
    created_at: str
```

- [ ] **Step 6: Write `sleuth/api/routes/chat.py`**

```python
from fastapi import APIRouter, HTTPException, Request

from sleuth.api.schemas import ChatOut, CreateChatIn, MessageOut
from sleuth.store import create_chat, get_chat, get_repo, list_chats, list_messages

router = APIRouter()


@router.post("/chats", response_model=ChatOut)
def create_chat_route(body: CreateChatIn, request: Request) -> ChatOut:
    conn = request.state.conn
    repo = get_repo(conn, body.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")
    chat_id = create_chat(conn, body.repo_id)
    conn.commit()
    return ChatOut(**[c for c in list_chats(conn, body.repo_id) if c["id"] == chat_id][0])


@router.get("/chats", response_model=list[ChatOut])
def get_chats_route(repo_id: str, request: Request) -> list[ChatOut]:
    return [ChatOut(**c) for c in list_chats(request.state.conn, repo_id)]


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
def get_messages_route(chat_id: str, request: Request) -> list[MessageOut]:
    conn = request.state.conn
    if get_chat(conn, chat_id) is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return [MessageOut(**m) for m in list_messages(conn, chat_id)]
```

- [ ] **Step 7: Register the router**

In `sleuth/api/main.py`:

```python
from sleuth.api.routes import chat, repos
...
    app.include_router(repos.router)
    app.include_router(chat.router)
```

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/test_api_chat.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add schema.sql sleuth/store.py sleuth/api tests/test_api_chat.py
git commit -m "feat: add chat/message persistence and CRUD endpoints"
```

---

## Task 4: Chat SSE streaming endpoint

**Files:**
- Modify: `sleuth/retrieve/answer.py` (`stream_answer` gains `on_sources`)
- Modify: `sleuth/api/routes/chat.py` (add `POST /chat`)
- Test: extend `tests/test_answer.py`; append to `tests/test_api_chat.py`

**Interfaces:**
- Consumes: Task 3's `create_message`, `get_chat`.
- Produces: `stream_answer(question, repo_id, conn, config, on_sources: Callable[[list[SearchResult]], None] | None = None)`. `POST /chat` body `{chat_id, question}` → SSE response: one `event: sources` frame (JSON list of `{file_path, symbol_name, kind, start_line, end_line}`), then per-token `data:` frames, then `event: done`. Consumed by Task 10 (Chat screen's `streamChat`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_answer.py
@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_reports_sources_via_callback(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
    )

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    captured = []
    tokens = [t async for t in stream_answer("q?", repo_id, pg_conn, config, on_sources=lambda results: captured.append(results))]

    assert "".join(tokens) == "hi"
    assert len(captured) == 1
    assert captured[0][0].file_path == "f.py"
```

```python
# append to tests/test_api_chat.py
@respx.mock
def test_post_chat_streams_tokens_and_persists_messages(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
    )

    client = _client()
    chat_id = client.post("/chats", json={"repo_id": repo_id}).json()["id"]

    with client.stream("POST", "/chat", json={"chat_id": chat_id, "question": "what does foo do?"}) as resp:
        body = "".join(resp.iter_text())

    assert "event: sources" in body
    assert "event: done" in body

    messages = client.get(f"/chats/{chat_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hi"
    assert messages[1]["sources"][0]["file_path"] == "f.py"


def test_post_chat_rejects_not_ready_repo(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()
    client = _client()
    resp = client.post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 409  # can't even create a chat yet — covered by Task 3's test
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_answer.py tests/test_api_chat.py -v -k "sources or streams_tokens"`
Expected: FAIL

- [ ] **Step 3: Add `on_sources` to `stream_answer`**

```python
async def stream_answer(question: str, repo_id: str, conn, config: Config, on_sources=None) -> AsyncIterator[str]:
    row = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None or row[0] != "ready":
        raise ValueError(f"Repo {repo_id} is not ready to query (status={row[0] if row else 'missing'})")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)
    query_vector = (await embedder.embed_batch([question]))[0]
    results = search_chunks(conn, repo_id, query_vector)
    if on_sources:
        on_sources(results)
    prompt = build_prompt(question, results)

    chain = get_fallback_chain(config)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    async for token in chat_with_fallback(chain, messages, stream=True):
        yield token
```

- [ ] **Step 4: Add `POST /chat` to `sleuth/api/routes/chat.py`**

```python
import json

from fastapi.responses import StreamingResponse

from sleuth.retrieve.answer import stream_answer
from sleuth.store import create_message


class SendMessageIn(BaseModel):
    chat_id: str
    question: str


@router.post("/chat")
async def post_chat(body: SendMessageIn, request: Request) -> StreamingResponse:
    conn = request.state.conn
    config = request.state.config
    chat = get_chat(conn, body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    repo = get_repo(conn, chat["repo_id"])
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")

    create_message(conn, body.chat_id, "user", body.question)
    conn.commit()

    async def event_stream():
        collected_sources: list[dict] = []

        def on_sources(results):
            collected_sources.extend(
                {
                    "file_path": r.file_path,
                    "symbol_name": r.symbol_name,
                    "kind": r.kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                }
                for r in results
            )
            yield_sources_event = f"event: sources\ndata: {json.dumps(collected_sources)}\n\n"
            chunks.append(yield_sources_event)

        chunks: list[str] = []
        answer_parts: list[str] = []
        async for token in stream_answer(body.question, chat["repo_id"], conn, config, on_sources=on_sources):
            for pending in chunks:
                yield pending
            chunks.clear()
            answer_parts.append(token)
            yield f"data: {token}\n\n"

        create_message(conn, body.chat_id, "assistant", "".join(answer_parts), sources=collected_sources)
        conn.commit()
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

`SendMessageIn` needs `from pydantic import BaseModel` imported at the top of the
file alongside the existing `ChatOut`/`CreateChatIn` imports from `schemas.py` —
either add it to `schemas.py` next to the others (preferred, keeps all request
models together) or import `BaseModel` directly in `chat.py`. Use `schemas.py`
for consistency with Tasks 1 and 3.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_answer.py tests/test_api_chat.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add sleuth/retrieve/answer.py sleuth/api/routes/chat.py sleuth/api/schemas.py tests/test_answer.py tests/test_api_chat.py
git commit -m "feat: add SSE chat endpoint with source citations and persistence"
```

---

## Task 5: Eval persistence schema + eval endpoints

**Files:**
- Modify: `schema.sql` (add `eval_runs` table)
- Modify: `sleuth/eval/runner.py` (`run_eval` returns `EvalSummary`; add `format_table`)
- Modify: `sleuth/cli.py` (update `eval` command for the new return type)
- Modify: `tests/test_eval_runner.py` (update assertions for `EvalSummary`)
- Modify: `sleuth/store.py` (add eval_run CRUD)
- Modify: `sleuth/api/schemas.py` (add `EvalRunOut`, `TriggerEvalIn`)
- Create: `sleuth/api/routes/eval.py`
- Modify: `sleuth/api/main.py` (include router)
- Test: `tests/test_api_eval.py`

**Interfaces:**
- Consumes: Task 1's `get_repo` pattern.
- Produces: `sleuth.eval.runner.EvalSummary` (fields `hit_rate: float, mrr: float, avg_judge: float | None, results: list[CaseResult]`), `sleuth.eval.runner.format_table(summary) -> str`, `store.create_eval_run(conn, repo_id, golden_yaml_path) -> str`, `store.update_eval_run_result(conn, eval_run_id, *, status, embedding_model=None, hit_rate=None, mrr=None, avg_judge=None, error_message=None) -> None`, `store.get_eval_run(conn, eval_run_id) -> dict | None`, `store.list_eval_runs(conn, repo_id) -> list[dict]`. Routes `POST /eval`, `GET /eval/{id}`, `GET /eval?repo_id=` — consumed by Task 11 (Eval screen).

- [ ] **Step 1: Write the failing tests**

Update `tests/test_eval_runner.py`'s existing assertion (the only breaking change in this task):

```python
    summary = await run_eval(str(golden_path), pg_conn, config)

    assert summary.hit_rate == 1.0
    assert summary.mrr == 1.0
    assert summary.avg_judge == 5.0
    table = format_table(summary)
    assert "hit-rate@8: 1.00" in table
```

And update the import line: `from sleuth.eval.runner import format_table, load_golden, run_eval`.

```python
# tests/test_api_eval.py
import httpx
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.store import create_repo, update_repo_status, upsert_chunks
from tests.conftest import TEST_DATABASE_URL

GOLDEN_YAML = """
repo: {repo_id}
cases:
  - question: "Where is foo defined?"
    expected_files: ["f.py"]
    expected_symbols: ["foo"]
    reference_answer: "foo is defined in f.py."
"""


def _client():
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url=TEST_DATABASE_URL)
    return TestClient(create_app(config))


@respx.mock
def test_trigger_and_poll_eval_run(pg_conn, tmp_path):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(GOLDEN_YAML.format(repo_id=repo_id))

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "5"}}]})
    )

    client = _client()
    created = client.post("/eval", json={"repo_id": repo_id, "golden_yaml_path": str(golden_path)}).json()
    assert created["status"] == "running"

    import time
    for _ in range(50):
        got = client.get(f"/eval/{created['id']}").json()
        if got["status"] in ("complete", "failed"):
            break
        time.sleep(0.1)

    assert got["status"] == "complete"
    assert got["hit_rate"] == 1.0

    listed = client.get(f"/eval?repo_id={repo_id}").json()
    assert listed[0]["id"] == created["id"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_eval_runner.py tests/test_api_eval.py -v`
Expected: FAIL

- [ ] **Step 3: Refactor `sleuth/eval/runner.py`**

```python
@dataclass
class EvalSummary:
    hit_rate: float
    mrr: float
    avg_judge: float | None
    results: list[CaseResult]


async def run_eval(golden_yaml_path: str, conn, config: Config) -> EvalSummary:
    repo_id, cases = load_golden(golden_yaml_path)
    row = conn.execute("SELECT id FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None:
        raise ValueError(f"Repo {repo_id} not found")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)
    chain = get_fallback_chain(config)
    judge = get_generator(config)

    results: list[CaseResult] = []
    for case in cases:
        query_vector = (await embedder.embed_batch([case.question]))[0]
        search_results = search_chunks(conn, repo_id, query_vector, top_k=TOP_K)
        hit, rr = _hit_and_rank(search_results, case)

        prompt = build_prompt(case.question, search_results)
        answer = "".join(
            [t async for t in chat_with_fallback(chain, [{"role": "user", "content": prompt}], stream=False)]
        )

        judge_score = None
        if case.reference_answer:
            judge_text = "".join(
                [
                    t async for t in judge.chat(
                        [{"role": "user", "content": JUDGE_PROMPT.format(reference=case.reference_answer, produced=answer)}],
                        stream=False,
                    )
                ]
            )
            judge_score = _parse_judge_score(judge_text)

        results.append(CaseResult(case.question, hit, rr, judge_score, answer))

    return _summarize(results)


def _summarize(results: list[CaseResult]) -> EvalSummary:
    if not results:
        return EvalSummary(0.0, 0.0, None, [])
    hit_rate = sum(1 for r in results if r.hit) / len(results)
    mrr = sum(r.reciprocal_rank for r in results) / len(results)
    scored = [r.judge_score for r in results if r.judge_score is not None]
    avg_judge = sum(scored) / len(scored) if scored else None
    return EvalSummary(hit_rate, mrr, avg_judge, results)


def format_table(summary: EvalSummary) -> str:
    if not summary.results:
        return "No cases to evaluate."
    lines = [f"{'question':50s}  {'hit':5s}  {'rr':5s}  {'judge':5s}"]
    for r in summary.results:
        judge_str = str(r.judge_score) if r.judge_score is not None else "-"
        lines.append(f"{r.question[:50]:50s}  {str(r.hit):5s}  {r.reciprocal_rank:.2f}  {judge_str:5s}")
    lines.append("")
    avg_judge_str = summary.avg_judge if summary.avg_judge is not None else "n/a"
    lines.append(f"hit-rate@{TOP_K}: {summary.hit_rate:.2f}   MRR: {summary.mrr:.2f}   avg judge: {avg_judge_str}")
    return "\n".join(lines)
```

Remove the old `_format_table` function (replaced by the two functions above).

- [ ] **Step 4: Update `sleuth/cli.py`**

```python
from sleuth.eval.runner import format_table, run_eval
...
        elif args.command == "eval":
            summary = await run_eval(args.golden_yaml_path, conn, config)
            print(format_table(summary))
```

- [ ] **Step 5: Add schema table**

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS eval_runs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id          uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    golden_yaml_path text NOT NULL,
    status           text NOT NULL DEFAULT 'running',
    embedding_model  text,
    hit_rate         double precision,
    mrr              double precision,
    avg_judge        double precision,
    error_message    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz
);
```

- [ ] **Step 6: Add CRUD to `sleuth/store.py`**

```python
def create_eval_run(conn: psycopg.Connection, repo_id: str, golden_yaml_path: str) -> str:
    row = conn.execute(
        "INSERT INTO eval_runs (repo_id, golden_yaml_path) VALUES (%s, %s) RETURNING id",
        (repo_id, golden_yaml_path),
    ).fetchone()
    return str(row[0])


def update_eval_run_result(
    conn: psycopg.Connection, eval_run_id: str, *, status: str, embedding_model: str | None = None,
    hit_rate: float | None = None, mrr: float | None = None, avg_judge: float | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE eval_runs
        SET status = %s, embedding_model = %s, hit_rate = %s, mrr = %s, avg_judge = %s,
            error_message = %s, completed_at = now()
        WHERE id = %s
        """,
        (status, embedding_model, hit_rate, mrr, avg_judge, error_message, eval_run_id),
    )


def get_eval_run(conn: psycopg.Connection, eval_run_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, repo_id, golden_yaml_path, status, embedding_model, hit_rate, mrr, avg_judge, "
        "error_message, created_at, completed_at FROM eval_runs WHERE id = %s",
        (eval_run_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ["id", "repo_id", "golden_yaml_path", "status", "embedding_model", "hit_rate", "mrr",
            "avg_judge", "error_message", "created_at", "completed_at"]
    values = [str(row[0]), str(row[1]), row[2], row[3], row[4], row[5], row[6], row[7], row[8],
              row[9].isoformat(), row[10].isoformat() if row[10] else None]
    return dict(zip(keys, values))


def list_eval_runs(conn: psycopg.Connection, repo_id: str) -> list[dict]:
    rows = conn.execute("SELECT id FROM eval_runs WHERE repo_id = %s ORDER BY created_at DESC", (repo_id,)).fetchall()
    return [get_eval_run(conn, str(row[0])) for row in rows]
```

- [ ] **Step 7: Add schemas**

Append to `sleuth/api/schemas.py`:

```python
class TriggerEvalIn(BaseModel):
    repo_id: str
    golden_yaml_path: str


class EvalRunOut(BaseModel):
    id: str
    repo_id: str
    golden_yaml_path: str
    status: str
    embedding_model: str | None = None
    hit_rate: float | None = None
    mrr: float | None = None
    avg_judge: float | None = None
    error_message: str | None = None
    created_at: str
    completed_at: str | None = None
```

- [ ] **Step 8: Write `sleuth/api/routes/eval.py`**

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from sleuth.api.schemas import EvalRunOut, TriggerEvalIn
from sleuth.db import get_connection
from sleuth.eval.runner import run_eval
from sleuth.store import create_eval_run, get_eval_run, get_repo, list_eval_runs, update_eval_run_result

router = APIRouter()


async def _run_eval_job(eval_run_id: str, golden_yaml_path: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    try:
        try:
            summary = await run_eval(golden_yaml_path, conn, config)
            update_eval_run_result(
                conn, eval_run_id, status="complete", embedding_model="voyage-code-3",
                hit_rate=summary.hit_rate, mrr=summary.mrr, avg_judge=summary.avg_judge,
            )
        except Exception as exc:
            update_eval_run_result(conn, eval_run_id, status="failed", error_message=str(exc))
        conn.commit()
    finally:
        conn.close()


@router.post("/eval", response_model=EvalRunOut)
def trigger_eval(body: TriggerEvalIn, request: Request, background_tasks: BackgroundTasks) -> EvalRunOut:
    conn = request.state.conn
    config = request.state.config
    if get_repo(conn, body.repo_id) is None:
        raise HTTPException(status_code=404, detail="repo not found")
    eval_run_id = create_eval_run(conn, body.repo_id, body.golden_yaml_path)
    conn.commit()
    background_tasks.add_task(_run_eval_job, eval_run_id, body.golden_yaml_path, config.database_url, config)
    return EvalRunOut(**get_eval_run(conn, eval_run_id))


@router.get("/eval/{eval_run_id}", response_model=EvalRunOut)
def get_eval_run_route(eval_run_id: str, request: Request) -> EvalRunOut:
    run = get_eval_run(request.state.conn, eval_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRunOut(**run)


@router.get("/eval", response_model=list[EvalRunOut])
def list_eval_runs_route(repo_id: str, request: Request) -> list[EvalRunOut]:
    return [EvalRunOut(**r) for r in list_eval_runs(request.state.conn, repo_id)]
```

`embedding_model="voyage-code-3"` is hardcoded here rather than read off the
repo, because the eval harness always uses `VoyageEmbedder` directly
(Tasks 7-8 scope note — no `get_embedder()` factory exists). If NIM embedding
is ever added, this line is exactly where a second provider's eval runs would
get tagged, feeding the Task 11 comparison chart's second bar.

- [ ] **Step 9: Register the router**

In `sleuth/api/main.py`:

```python
from sleuth.api.routes import chat, eval as eval_routes, repos
...
    app.include_router(eval_routes.router)
```

- [ ] **Step 10: Run to verify it passes**

Run: `pytest tests/test_eval_runner.py tests/test_api_eval.py tests/test_cli.py -v`
Expected: PASS (re-run `test_cli.py` too since `cli.py`'s eval command changed).

- [ ] **Step 11: Commit**

```bash
git add schema.sql sleuth/eval/runner.py sleuth/cli.py sleuth/store.py sleuth/api tests/test_eval_runner.py tests/test_api_eval.py
git commit -m "feat: add eval run persistence and endpoints"
```

---

## Task 6: Vite scaffold + design tokens + routing shell

**Files:**
- Create: `web/` (Vite React scaffold via `npm create vite@latest web -- --template react`)
- Create: `web/src/theme.css`, `web/src/api.js`, `web/src/App.jsx`, `web/src/components/NavRail.jsx`, `web/src/components/AppShell.jsx`
- Create: `web/.env.example`

**Interfaces:**
- Consumes: nothing from `sleuth/` yet (backend only reached via `fetch` in Task 8+).
- Produces: CSS custom properties from the Design System section above, importable by every later component. `AppShell` renders `<NavRail/>` + `<Outlet/>` — every app screen (Tasks 9-11) renders inside it. `api.js` exports the base `fetch` wrapper (`apiUrl(path)`) other tasks build on.

- [ ] **Step 1: Scaffold Vite**

Run (Windows/Git Bash, `web/` doesn't exist yet):
```bash
npm create vite@latest web -- --template react
cd web && npm install react-router-dom
```

- [ ] **Step 2: Write `web/.env.example`**

```
VITE_API_URL=http://localhost:8000
```

- [ ] **Step 3: Write `web/src/theme.css`**

```css
:root {
  --bg: oklch(0.17 0.014 250);
  --panel: oklch(0.14 0.012 250);
  --panel-alt: oklch(0.155 0.012 250);
  --text: oklch(0.95 0.006 250);
  --text-secondary: oklch(0.75 0.01 250);
  --text-muted: oklch(0.6 0.012 250);
  --text-faint: oklch(0.55 0.01 250);
  --border: oklch(1 0 0 / 0.07);
  --border-strong: oklch(1 0 0 / 0.14);
  --accent: oklch(0.62 0.10 148);
  --accent-hover: oklch(0.68 0.10 148);
  --accent-wash: oklch(0.62 0.10 148 / 0.14);
  --accent-on: oklch(0.15 0.02 148);
  --status-ready: oklch(0.75 0.16 150);
  --status-ready-wash: oklch(0.72 0.16 150 / 0.14);
  --status-neutral: oklch(0.65 0.01 250);
  --status-neutral-wash: oklch(0.6 0.01 250 / 0.14);
  --compare-secondary: oklch(0.72 0.19 275);
  --font-mono: 'IBM Plex Mono', monospace;
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-sans); }
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
@keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }

.icon-rail { width: 72px; height: 100%; background: var(--panel); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; align-items: center; padding: 20px 0; gap: 28px; flex-shrink: 0; }
.icon-rail a { width: 40px; height: 40px; border-radius: 9px; display: flex; align-items: center;
  justify-content: center; }
.icon-rail a:hover { background: oklch(1 0 0 / 0.06); }
.icon-rail a.active { background: var(--accent-wash); }

.app-shell { display: flex; height: 100vh; overflow: hidden; }
.app-content { flex: 1; overflow-y: auto; padding: 48px 56px; }

.card { background: var(--panel); border: 1px solid var(--border-strong); border-radius: 12px; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 100px;
  font-size: 12px; font-weight: 600; }
.pill-ready { background: var(--status-ready-wash); color: var(--status-ready); }
.pill-indexing { background: var(--accent-wash); color: var(--accent-hover); }
.pill-failed { background: var(--status-neutral-wash); color: var(--status-neutral); opacity: 0.85; }
.dot { width: 6px; height: 6px; border-radius: 50%; }
.dot-pulse { animation: pulse 1.4s ease-in-out infinite; }

.btn-primary { padding: 12px 22px; background: var(--accent); color: var(--accent-on); border-radius: 8px;
  font-weight: 600; font-size: 13.5px; cursor: pointer; border: none; white-space: nowrap; }
.btn-primary:hover { background: var(--accent-hover); }
.input-mono { flex: 1; padding: 12px 16px; background: var(--panel); border: 1px solid var(--border-strong);
  border-radius: 8px; font-family: var(--font-mono); font-size: 13.5px; color: var(--text); outline: none; }
.input-mono:focus { border-color: oklch(0.62 0.10 148 / 0.6); }
```

- [ ] **Step 4: Write `web/src/api.js`**

```javascript
const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

export async function apiGet(path) {
  const res = await fetch(apiUrl(path));
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiPost(path, body) {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const err = new Error(detail.detail || `POST ${path} failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}
```

- [ ] **Step 5: Write `web/src/components/NavRail.jsx`**

```jsx
import { NavLink } from 'react-router-dom';

export default function NavRail() {
  return (
    <div className="icon-rail">
      <NavLink to="/">
        <svg width="24" height="24" viewBox="0 0 26 26" fill="none">
          <circle cx="11" cy="11" r="7.5" stroke="var(--accent)" strokeWidth="2" />
          <line x1="16.3" y1="16.3" x2="23" y2="23" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          <circle cx="11" cy="11" r="2" fill="var(--accent)" />
        </svg>
      </NavLink>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
        <NavLink to="/app/repos" title="Repos" className={({ isActive }) => (isActive ? 'active' : '')}>
          <span aria-hidden style={{ fontFamily: 'var(--font-mono)', fontSize: 16 }}>≡</span>
        </NavLink>
        <NavLink to="/app/indexing" title="Indexing" className={({ isActive }) => (isActive ? 'active' : '')}>
          <span aria-hidden style={{ fontFamily: 'var(--font-mono)', fontSize: 16 }}>◐</span>
        </NavLink>
        <NavLink to="/app/chat" title="Chat" className={({ isActive }) => (isActive ? 'active' : '')}>
          <span aria-hidden style={{ fontFamily: 'var(--font-mono)', fontSize: 16 }}>💬</span>
        </NavLink>
        <NavLink to="/app/eval" title="Eval" className={({ isActive }) => (isActive ? 'active' : '')}>
          <span aria-hidden style={{ fontFamily: 'var(--font-mono)', fontSize: 16 }}>▁▄▂</span>
        </NavLink>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Write `web/src/components/AppShell.jsx`**

```jsx
import { Outlet } from 'react-router-dom';
import NavRail from './NavRail';

export default function AppShell() {
  return (
    <div className="app-shell">
      <NavRail />
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write `web/src/App.jsx`** (screens are stubs until Tasks 7-11 fill them in)

```jsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/AppShell';
import LandingPage from './components/LandingPage';
import RepoList from './components/RepoList';
import IndexingScreen from './components/IndexingScreen';
import ChatScreen from './components/ChatScreen';
import EvalScreen from './components/EvalScreen';
import './theme.css';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/app" element={<AppShell />}>
          <Route index element={<Navigate to="repos" replace />} />
          <Route path="repos" element={<RepoList />} />
          <Route path="indexing/:repoId?" element={<IndexingScreen />} />
          <Route path="chat/:repoId?" element={<ChatScreen />} />
          <Route path="eval/:repoId?" element={<EvalScreen />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 8: Add IBM Plex Mono to `web/index.html`**

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

- [ ] **Step 9: Manual test**

Run: `cd web && npm run dev`
Expected: blank dark page at `/`, navigating to `/app/repos` shows the icon rail with no crash (screens are stubs — filled in next tasks).

- [ ] **Step 10: Commit**

```bash
git add web
git commit -m "feat: scaffold Vite React app with design tokens and routing shell"
```

---

## Task 7: Landing page

**Files:**
- Create: `web/src/components/LandingPage.jsx`

**Interfaces:**
- Consumes: `theme.css` tokens only. No backend calls — this is the marketing page from `Sleuth.dc.html`, its Repos/Indexing/Chat panels are illustrative mockups (matching the source design), not live data.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write `web/src/components/LandingPage.jsx`**

Translate `Sleuth.dc.html` section-by-section into JSX using `theme.css` classes plus
targeted inline styles for one-off layout (grid columns, gaps) — matching the
source's copy, structure, and animation beats:
- Sticky nav (logo/wordmark, "How it works" / "Eval results" links, GitHub pill) — add
  a scroll listener (`useEffect` + `window.addEventListener('scroll', ...)`) that
  toggles a `scrolled` class applying the blurred/bordered background, mirroring
  `Sleuth.dc.html`'s `navStyle`.
- Hero: headline "Point it at a repo.<br/>Ask it anything.", CTA buttons ("Try Sleuth"
  → `/app/repos`, "Read the spec" → link to the design doc's GitHub path or a no-op
  for now), and the autotyping terminal panel — implement the typing effect with a
  `useEffect` + `setInterval` reproducing `Sleuth.dc.html`'s `tickHero()` logic against
  the same four `HERO_LINES` strings.
- "How it works": 5-step Clone→Parse→Chunk→Embed→Store row with the connecting line,
  same copy as the source.
- Repo list mock, indexing status mock, chat mock (with the same autotyping +
  fade-in-citations behavor as `tickChat()`) — reuse the exact copy/values from
  `Sleuth.dc.html` (e.g. `facebook/react`, `1,842 chunks`, the React re-render Q&A).
- Footer with the tech-stack tags (`Python`, `tree-sitter`, `pgvector`, `Groq`, `React`).

Use `IntersectionObserver` for the fade/slide-in-on-scroll behavior on each section,
matching the source's `data-reveal` + `reveal(id)` helper.

- [ ] **Step 2: Manual test**

Run: `npm run dev`, open `/`.
Expected: nav blurs on scroll, hero terminal autotypes and loops, sections fade in as
you scroll past them, chat mock's answer types out with citations fading in after.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/LandingPage.jsx
git commit -m "feat: add landing page"
```

---

## Task 8: Repos screen (real data)

**Files:**
- Create: `web/src/components/RepoList.jsx`, `web/src/components/AddRepoForm.jsx`, `web/src/components/RepoStatusBadge.jsx`
- Modify: `web/src/api.js` (add `listRepos`, `addRepo`)

**Interfaces:**
- Consumes: Task 1's `GET /repos`, `POST /repos` (via `web/src/api.js`).
- Produces: nothing consumed by later tasks (Chat/Eval screens fetch repos independently via the same `listRepos` helper).

- [ ] **Step 1: Add repo calls to `web/src/api.js`**

```javascript
export function listRepos() {
  return apiGet('/repos');
}

export function addRepo(githubUrl) {
  return apiPost('/repos', { github_url: githubUrl });
}
```

- [ ] **Step 2: Write `web/src/components/RepoStatusBadge.jsx`**

```jsx
export default function RepoStatusBadge({ status }) {
  const cls = status === 'ready' ? 'pill pill-ready' : status === 'indexing' ? 'pill pill-indexing' : status === 'failed' ? 'pill pill-failed' : 'pill pill-indexing';
  const dotCls = status === 'indexing' ? 'dot dot-pulse' : 'dot';
  return (
    <div className={cls}>
      <span className={dotCls} style={{ background: 'currentColor' }} />
      {status}
    </div>
  );
}
```

- [ ] **Step 3: Write `web/src/components/AddRepoForm.jsx`**

```jsx
import { useState } from 'react';
import { addRepo } from '../api';

export default function AddRepoForm({ onAdded }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState(null);

  async function handleAdd() {
    if (!url.trim()) return;
    try {
      const repo = await addRepo(url.trim());
      setUrl('');
      setError(null);
      onAdded(repo);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', gap: 10 }}>
        <input
          className="input-mono"
          placeholder="github.com/owner/repo"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
        />
        <button className="btn-primary" onClick={handleAdd}>Index repo</button>
      </div>
      {error && <div style={{ color: 'var(--status-neutral)', fontSize: 12.5, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/components/RepoList.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRepos } from '../api';
import AddRepoForm from './AddRepoForm';
import RepoStatusBadge from './RepoStatusBadge';

function repoDetail(repo) {
  if (repo.status === 'failed') return repo.error_message || 'indexing failed';
  if (repo.status === 'ready') return `${repo.embedding_model || 'voyage-code-3'} · ready`;
  return 'indexing…';
}

export default function RepoList() {
  const [repos, setRepos] = useState([]);

  async function refresh() {
    setRepos(await listRepos());
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: 920, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>Repos</h1>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--text-faint)' }}>
          {repos.filter((r) => r.status === 'ready').length} indexed
        </span>
      </div>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 28 }}>
        Point Sleuth at a GitHub URL. It clones, parses, and indexes it in the background.
      </p>

      <AddRepoForm onAdded={(repo) => setRepos((prev) => [repo, ...prev])} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {repos.map((repo) => {
          const name = repo.github_url.replace(/^https?:\/\/github\.com\//, '');
          return (
            <div key={repo.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div style={{ width: 36, height: 36, borderRadius: 8, background: 'oklch(1 0 0 / 0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                  {name.charAt(0).toUpperCase()}
                </div>
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14.5, fontWeight: 500, marginBottom: 4 }}>{name}</div>
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>{repoDetail(repo)}</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <RepoStatusBadge status={repo.status} />
                {repo.status !== 'ready' && (
                  <Link to={`/app/indexing/${repo.id}`} style={{ fontSize: 12, color: 'var(--accent)' }}>watch →</Link>
                )}
                {repo.status === 'ready' && (
                  <Link to={`/app/chat/${repo.id}`} style={{ fontSize: 12, color: 'var(--accent)' }}>chat →</Link>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Manual test**

Run: `uvicorn sleuth.api.main:app --reload` (backend) + `npm run dev` (frontend), add a
real small repo, confirm it appears immediately as `pending`/`indexing`, and flips to
`ready` (or `failed`) within the poll interval without a page reload.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/RepoList.jsx web/src/components/AddRepoForm.jsx web/src/components/RepoStatusBadge.jsx web/src/api.js
git commit -m "feat: add Repos screen wired to the real API"
```

---

## Task 9: Indexing screen (real data)

**Files:**
- Create: `web/src/components/IndexingScreen.jsx`
- Modify: `web/src/api.js` (add `getRepo`, `getProgress`)

**Interfaces:**
- Consumes: Task 2's `GET /repos/{id}/progress`, Task 1's `GET /repos/{id}`.

- [ ] **Step 1: Add calls to `web/src/api.js`**

```javascript
export function getRepo(repoId) {
  return apiGet(`/repos/${repoId}`);
}

export function getProgress(repoId) {
  return apiGet(`/repos/${repoId}/progress`);
}
```

- [ ] **Step 2: Write `web/src/components/IndexingScreen.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getProgress, getRepo } from '../api';

const STEP_ORDER = ['cloning', 'cloned', 'parsed', 'chunked', 'embedding_start', 'embedding_progress', 'stored', 'ready'];
const STEP_LABELS = { cloning: 'Clone', cloned: 'Clone', parsed: 'Parse', chunked: 'Chunk', embedding_start: 'Embed', embedding_progress: 'Embed', stored: 'Store', ready: 'Store' };
const DISPLAY_STEPS = ['Clone', 'Parse', 'Chunk', 'Embed', 'Store'];

function stepIndex(step) {
  const label = STEP_LABELS[step] || 'Clone';
  return DISPLAY_STEPS.indexOf(label);
}

export default function IndexingScreen() {
  const { repoId } = useParams();
  const [repo, setRepo] = useState(null);
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    if (!repoId) return;
    let cancelled = false;
    async function poll() {
      const [r, p] = await Promise.all([getRepo(repoId), getProgress(repoId)]);
      if (!cancelled) {
        setRepo(r);
        setProgress(p);
      }
    }
    poll();
    const interval = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [repoId]);

  if (!repoId) return <p style={{ color: 'var(--text-muted)' }}>Select a repo from the Repos screen to watch its indexing progress.</p>;
  if (!repo || !progress) return <p style={{ color: 'var(--text-muted)' }}>Loading…</p>;

  const activeIdx = stepIndex(progress.step);
  const detail = progress.detail || {};

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>Indexing</h1>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--accent-hover)' }}>
          {repo.github_url.replace(/^https?:\/\/github\.com\//, '')}
        </span>
      </div>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 32 }}>
        Incremental re-index via content_hash — unchanged chunks reuse their stored embedding.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', position: 'relative', marginBottom: 40 }}>
        <div style={{ position: 'absolute', top: 19, left: '6%', right: '6%', height: 1, background: 'var(--border-strong)', zIndex: 0 }} />
        {DISPLAY_STEPS.map((label, i) => {
          const done = i < activeIdx || repo.status === 'ready';
          const active = i === activeIdx && repo.status !== 'ready';
          return (
            <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: 14, position: 'relative', zIndex: 1 }}>
              <div
                className={active ? 'dot-pulse' : ''}
                style={{
                  width: 38, height: 38, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: 13,
                  background: done ? 'var(--status-ready-wash)' : 'var(--bg)',
                  border: `1.5px solid ${done ? 'var(--status-ready)' : active ? 'var(--accent)' : 'var(--border-strong)'}`,
                  color: done ? 'var(--status-ready)' : active ? 'var(--accent)' : 'var(--text-faint)',
                }}
              >
                {done ? '✓' : i + 1}
              </div>
              <div style={{ fontWeight: 600, fontSize: 14.5, color: active ? 'var(--text)' : done ? 'var(--text-secondary)' : 'var(--text-faint)' }}>{label}</div>
            </div>
          );
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 24 }}>
        <div className="card">
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-faint)' }}>live log</div>
          <div style={{ padding: 20, fontFamily: 'var(--font-mono)', fontSize: 12.5, lineHeight: 2, height: 260, overflowY: 'auto', color: 'var(--text-muted)' }}>
            {progress.log.map((entry, i) => (
              <div key={i}>{entry.step} {JSON.stringify(Object.fromEntries(Object.entries(entry).filter(([k]) => k !== 'step')))}</div>
            ))}
          </div>
        </div>

        <div className="card" style={{ padding: 26, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Stat label="Files scanned" value={detail.files ?? '—'} />
          <Stat label="Chunks created" value={detail.chunks ?? '—'} />
          <Stat label="Skipped (unchanged)" value={detail.skipped_unchanged ?? '—'} accent />
          <Stat label="Embedding model" value={repo.embedding_model || 'voyage-code-3'} />
          <Stat label="Elapsed" value={`${Math.round(progress.elapsed_seconds)}s`} />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: accent ? 'var(--status-ready)' : 'var(--text)' }}>{value}</span>
    </div>
  );
}
```

- [ ] **Step 3: Manual test**

Add a repo from the Repos screen, click "watch →", confirm the step tracker advances
in near-real-time, the live log grows, and elapsed time ticks up; confirm it settles
on "Store" done once the repo flips to `ready`.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/IndexingScreen.jsx web/src/api.js
git commit -m "feat: add Indexing screen wired to live progress"
```

---

## Task 10: Chat screen (real data)

**Files:**
- Create: `web/src/components/ChatScreen.jsx`, `web/src/components/ChatSidebar.jsx`, `web/src/components/MessageList.jsx`, `web/src/components/Composer.jsx`
- Modify: `web/src/api.js` (add `listChats`, `createChat`, `getMessages`, `streamChat`)

**Interfaces:**
- Consumes: Task 3's chat CRUD endpoints, Task 4's `POST /chat` SSE endpoint, Task 1's `listRepos`.

- [ ] **Step 1: Add calls to `web/src/api.js`**

```javascript
export function listChats(repoId) {
  return apiGet(`/chats?repo_id=${repoId}`);
}

export function createChat(repoId) {
  return apiPost('/chats', { repo_id: repoId });
}

export function getMessages(chatId) {
  return apiGet(`/chats/${chatId}/messages`);
}

export async function streamChat(chatId, question, { onSources, onToken, onDone }) {
  const res = await fetch(apiUrl('/chat'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, question }),
  });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let frameEnd;
    while ((frameEnd = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      const lines = frame.split('\n');
      const eventLine = lines.find((l) => l.startsWith('event: '));
      const dataLine = lines.find((l) => l.startsWith('data: '));
      const eventType = eventLine ? eventLine.slice('event: '.length) : 'message';
      const data = dataLine ? dataLine.slice('data: '.length) : '';

      if (eventType === 'sources') onSources(JSON.parse(data));
      else if (eventType === 'done') onDone();
      else onToken(data);
    }
  }
}
```

- [ ] **Step 2: Write `web/src/components/MessageList.jsx`**

```jsx
export default function MessageList({ messages, streamingText, thinking }) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 32, display: 'flex', flexDirection: 'column', gap: 22 }}>
      {messages.map((m) => (
        <MessageRow key={m.id} role={m.role} text={m.content} sources={m.sources} />
      ))}
      {thinking && <MessageRow role="assistant" text="" thinking />}
      {streamingText !== null && <MessageRow role="assistant" text={streamingText} streaming />}
    </div>
  );
}

function MessageRow({ role, text, sources, thinking, streaming }) {
  const isUser = role === 'user';
  return (
    <div style={{ alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: isUser ? '70%' : '78%' }}>
      <div style={{ display: 'flex', gap: 10, flexDirection: isUser ? 'row-reverse' : 'row' }}>
        <div style={{
          width: 26, height: 26, borderRadius: '50%', flexShrink: 0, background: 'var(--accent-wash)',
          border: '1px solid oklch(0.62 0.10 148 / 0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--accent)',
        }}>
          {isUser ? 'Y' : 'S'}
        </div>
        <div style={isUser
          ? { background: 'var(--accent-wash)', border: '1px solid oklch(0.62 0.10 148 / 0.3)', padding: '12px 16px', borderRadius: '12px 12px 2px 12px', fontSize: 14 }
          : { background: 'var(--panel)', border: '1px solid var(--border)', padding: '14px 18px', borderRadius: '2px 14px 14px 14px', fontSize: 14, lineHeight: 1.65 }
        }>
          {thinking ? <span style={{ color: 'var(--text-muted)' }}>Thinking…</span> : text}
          {streaming && <span style={{ animation: 'blink 1s step-start infinite' }}>▊</span>}
        </div>
      </div>
      {sources && sources.length > 0 && (
        <div style={{ marginTop: 10, marginLeft: 36 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', marginBottom: 6 }}>SOURCES</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sources.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: 'oklch(1 0 0 / 0.035)', border: '1px solid var(--border)', borderRadius: 8, maxWidth: 520 }}>
                <span style={{ width: 5, height: 5, background: 'var(--accent)' }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.file_path}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)', whiteSpace: 'nowrap' }}>L{s.start_line}–{s.end_line}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Write `web/src/components/Composer.jsx`**

```jsx
import { useState } from 'react';

export default function Composer({ onSend, disabled, modelName }) {
  const [draft, setDraft] = useState('');
  const canSend = draft.trim().length > 0 && !disabled;

  function handleSend() {
    if (!canSend) return;
    onSend(draft.trim());
    setDraft('');
  }

  return (
    <div style={{ padding: '16px 28px 26px' }}>
      <div style={{ background: 'var(--panel-alt)', border: '1px solid var(--border-strong)', borderRadius: 18, padding: '6px 6px 10px' }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
          placeholder="Ask about this repo…"
          rows={2}
          style={{ width: '100%', background: 'transparent', border: 'none', outline: 'none', resize: 'none', color: 'var(--text)', fontSize: 14.5, lineHeight: 1.55, fontFamily: 'inherit', padding: '10px 12px 6px' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px 0', borderTop: '1px solid var(--border)', marginTop: 2 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, padding: '4px 10px', borderRadius: 6, background: 'var(--accent-wash)', color: 'var(--accent)' }}>{modelName}</span>
          <button
            onClick={handleSend}
            disabled={!canSend}
            style={{
              width: 36, height: 36, borderRadius: '50%', border: 'none', cursor: canSend ? 'pointer' : 'default',
              background: canSend ? 'var(--accent)' : 'oklch(1 0 0 / 0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/components/ChatSidebar.jsx`**

```jsx
export default function ChatSidebar({ repos, activeRepoId, onSelectRepo, chats, activeChatId, onSelectChat, onNewChat }) {
  return (
    <div style={{ width: 270, borderRight: '1px solid var(--border)', flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--panel)' }}>
      <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', marginBottom: 10 }}>REPO</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {repos.map((r) => (
            <div
              key={r.id}
              onClick={() => onSelectRepo(r.id)}
              style={{
                padding: '6px 12px', borderRadius: 100, fontFamily: 'var(--font-mono)', fontSize: 11.5, cursor: 'pointer',
                background: r.id === activeRepoId ? 'var(--accent-wash)' : 'oklch(1 0 0 / 0.05)',
                color: r.id === activeRepoId ? 'var(--accent)' : 'var(--text-muted)',
              }}
            >
              {r.github_url.replace(/^https?:\/\/github\.com\//, '')}
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 14 }}>
        <div onClick={onNewChat} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 12px', borderRadius: 10, fontSize: 13, cursor: 'pointer', marginBottom: 16, border: '1px dashed oklch(0.62 0.10 148 / 0.45)', color: 'var(--accent)' }}>
          + New chat
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', padding: '0 4px', marginBottom: 8 }}>CHATS</div>
        {chats.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelectChat(c.id)}
            style={{
              padding: '11px 12px 11px 14px', borderRadius: 8, cursor: 'pointer', marginBottom: 4,
              borderLeft: `2px solid ${c.id === activeChatId ? 'var(--accent)' : 'transparent'}`,
              background: c.id === activeChatId ? 'oklch(1 0 0 / 0.06)' : 'transparent',
            }}
          >
            <div style={{ fontSize: 13, color: c.id === activeChatId ? 'var(--text)' : 'var(--text-secondary)' }}>{c.title}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>{c.message_count} messages</div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `web/src/components/ChatScreen.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { createChat, getMessages, listChats, listRepos, streamChat } from '../api';
import ChatSidebar from './ChatSidebar';
import Composer from './Composer';
import MessageList from './MessageList';

export default function ChatScreen() {
  const { repoId } = useParams();
  const navigate = useNavigate();
  const [repos, setRepos] = useState([]);
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [streamingText, setStreamingText] = useState(null);
  const [thinking, setThinking] = useState(false);
  const [pendingSources, setPendingSources] = useState(null);

  useEffect(() => {
    listRepos().then((all) => {
      const ready = all.filter((r) => r.status === 'ready');
      setRepos(ready);
      if (!repoId && ready.length > 0) navigate(`/app/chat/${ready[0].id}`, { replace: true });
    });
  }, []);

  useEffect(() => {
    if (!repoId) return;
    listChats(repoId).then((cs) => {
      setChats(cs);
      setActiveChatId(cs[0]?.id ?? null);
    });
  }, [repoId]);

  useEffect(() => {
    if (!activeChatId) { setMessages([]); return; }
    getMessages(activeChatId).then(setMessages);
  }, [activeChatId]);

  async function handleNewChat() {
    const chat = await createChat(repoId);
    setChats((prev) => [{ ...chat, message_count: 0 }, ...prev]);
    setActiveChatId(chat.id);
  }

  async function handleSend(question) {
    let chatId = activeChatId;
    if (!chatId) {
      const chat = await createChat(repoId);
      setChats((prev) => [{ ...chat, message_count: 0 }, ...prev]);
      setActiveChatId(chat.id);
      chatId = chat.id;
    }

    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: 'user', content: question, sources: null }]);
    setThinking(true);
    setStreamingText(null);
    setPendingSources(null);

    let text = '';
    await streamChat(chatId, question, {
      onSources: (sources) => setPendingSources(sources),
      onToken: (token) => { setThinking(false); text += token; setStreamingText(text); },
      onDone: () => {
        setMessages((prev) => [...prev, { id: `local-${Date.now()}-a`, role: 'assistant', content: text, sources: pendingSources }]);
        setStreamingText(null);
        setThinking(false);
      },
    });
  }

  if (repos.length === 0) {
    return <p style={{ color: 'var(--text-muted)' }}>No indexed repos yet — add one from the Repos screen first.</p>;
  }

  const activeChat = chats.find((c) => c.id === activeChatId);

  return (
    <div style={{ display: 'flex', height: '100%', margin: '-48px -56px' }}>
      <ChatSidebar
        repos={repos}
        activeRepoId={repoId}
        onSelectRepo={(id) => navigate(`/app/chat/${id}`)}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={setActiveChatId}
        onNewChat={handleNewChat}
      />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '16px 28px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="dot" style={{ background: 'var(--status-ready)' }} />
          <span style={{ fontSize: 13.5 }}>{repos.find((r) => r.id === repoId)?.github_url.replace(/^https?:\/\/github\.com\//, '')}</span>
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>/ {activeChat?.title || 'New chat'}</span>
        </div>
        <MessageList messages={messages} streamingText={streamingText} thinking={thinking} />
        <Composer onSend={handleSend} modelName="voyage-code-3" />
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Manual test**

Add a repo, wait for `ready`, go to `/app/chat`, ask a question, confirm: thinking
indicator shows until the first token, tokens stream into the assistant bubble,
sources appear once streaming finishes, "New chat" starts a fresh thread, and
reloading the page still shows prior chats/messages (persistence).

- [ ] **Step 7: Commit**

```bash
git add web/src/components/ChatScreen.jsx web/src/components/ChatSidebar.jsx web/src/components/MessageList.jsx web/src/components/Composer.jsx web/src/api.js
git commit -m "feat: add Chat screen wired to SSE streaming and persisted history"
```

---

## Task 11: Eval screen (real data)

**Files:**
- Create: `web/src/components/EvalScreen.jsx`, `web/src/components/EvalBarChart.jsx`
- Modify: `web/src/api.js` (add `listEvalRuns`, `triggerEval`)

**Interfaces:**
- Consumes: Task 5's `POST /eval`, `GET /eval?repo_id=`.

- [ ] **Step 1: Add calls to `web/src/api.js`**

```javascript
export function listEvalRuns(repoId) {
  return apiGet(`/eval?repo_id=${repoId}`);
}

export function triggerEval(repoId, goldenYamlPath) {
  return apiPost('/eval', { repo_id: repoId, golden_yaml_path: goldenYamlPath });
}
```

- [ ] **Step 2: Write `web/src/components/EvalBarChart.jsx`**

Takes a list of `{ label, providers: [{ name, value, color }] }` rows — currently
`providers` has one entry (Voyage) per Task 5's decision; a second entry (e.g.
NIM/Nemotron) renders automatically as an extra bar + legend swatch, no code
change needed here if/when that eval data exists.

```jsx
export default function EvalBarChart({ rows }) {
  return (
    <div className="card" style={{ padding: 28, marginBottom: 32 }}>
      <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 20 }}>Embedding model comparison</div>
      {rows.map((row) => (
        <div key={row.label} style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>{row.label}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {row.providers.map((p) => (
              <div key={p.name} style={{ height: 22, background: 'oklch(1 0 0 / 0.06)', borderRadius: 5, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${p.value}%`, background: p.color, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', padding: '0 10px', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  {p.rawLabel}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 20, fontSize: 12.5, color: 'var(--text-faint)', marginTop: 8 }}>
        {[...new Map(rows.flatMap((r) => r.providers).map((p) => [p.name, p])).values()].map((p) => (
          <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: p.color }} />
            {p.name}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write `web/src/components/EvalScreen.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { listEvalRuns, listRepos, triggerEval } from '../api';
import EvalBarChart from './EvalBarChart';

const TOP_K = 8; // matches sleuth/eval/runner.py::TOP_K — keep in sync if that constant changes

export default function EvalScreen() {
  const { repoId } = useParams();
  const navigate = useNavigate();
  const [repos, setRepos] = useState([]);
  const [runs, setRuns] = useState([]);
  const [goldenPath, setGoldenPath] = useState('');

  useEffect(() => {
    listRepos().then((all) => {
      const ready = all.filter((r) => r.status === 'ready');
      setRepos(ready);
      if (!repoId && ready.length > 0) navigate(`/app/eval/${ready[0].id}`, { replace: true });
    });
  }, []);

  useEffect(() => {
    if (!repoId) return;
    const interval = setInterval(() => listEvalRuns(repoId).then(setRuns), 2000);
    listEvalRuns(repoId).then(setRuns);
    return () => clearInterval(interval);
  }, [repoId]);

  if (repos.length === 0) {
    return <p style={{ color: 'var(--text-muted)' }}>No indexed repos yet — add one from the Repos screen first.</p>;
  }

  const latest = runs.find((r) => r.status === 'complete');
  const byModel = {};
  for (const run of runs) {
    if (run.status === 'complete' && !(run.embedding_model in byModel)) byModel[run.embedding_model] = run;
  }
  const colors = { 'voyage-code-3': 'var(--accent)', 'nemotron-3-embed-1b': 'var(--compare-secondary)' };
  const rows = [
    { key: 'hit_rate', label: `hit-rate@${TOP_K}` },
    { key: 'mrr', label: 'MRR' },
    { key: 'avg_judge', label: 'answer quality (1–5)' },
  ].map(({ key, label }) => ({
    label,
    providers: Object.values(byModel).map((run) => ({
      name: run.embedding_model,
      color: colors[run.embedding_model] || 'var(--accent)',
      value: key === 'avg_judge' ? (run[key] / 5) * 100 : run[key] * 100,
      rawLabel: run[key]?.toFixed(2),
    })),
  }));

  return (
    <div style={{ maxWidth: 1020, margin: '0 auto' }}>
      <h1 style={{ fontSize: 28, fontWeight: 600, margin: '0 0 6px' }}>Eval harness</h1>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 32 }}>
        sleuth eval — every retrieval-affecting change re-runs against the golden sets before merge.
      </p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 32 }}>
        <input
          className="input-mono"
          placeholder="path/to/golden.yaml"
          value={goldenPath}
          onChange={(e) => setGoldenPath(e.target.value)}
        />
        <button className="btn-primary" onClick={() => goldenPath.trim() && triggerEval(repoId, goldenPath.trim())}>
          Run eval
        </button>
      </div>

      {latest && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 32 }}>
          <StatTile label={`hit-rate@${TOP_K}`} value={latest.hit_rate.toFixed(2)} />
          <StatTile label="MRR" value={latest.mrr.toFixed(2)} />
          <StatTile label="answer quality" value={latest.avg_judge != null ? `${latest.avg_judge.toFixed(1)}/5` : '—'} />
        </div>
      )}

      {Object.keys(byModel).length > 0 && <EvalBarChart rows={rows} />}

      <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 14 }}>Runs</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1, background: 'var(--border)', borderRadius: 10, overflow: 'hidden', border: '1px solid var(--border)' }}>
        {runs.map((r) => (
          <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px', background: 'var(--panel)' }}>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 500, marginBottom: 3 }}>{r.golden_yaml_path}</div>
              <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>{new Date(r.created_at).toLocaleString()}</div>
            </div>
            <div className={r.status === 'complete' ? 'pill pill-ready' : r.status === 'failed' ? 'pill pill-failed' : 'pill pill-indexing'}>
              {r.status}
            </div>
          </div>
        ))}
        {runs.length === 0 && <div style={{ padding: 20, color: 'var(--text-faint)', background: 'var(--panel)' }}>No eval runs yet.</div>}
      </div>
    </div>
  );
}

function StatTile({ label, value }) {
  return (
    <div className="card" style={{ padding: 22 }}>
      <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginBottom: 8 }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 600, color: 'var(--accent-hover)' }}>{value}</div>
    </div>
  );
}
```

- [ ] **Step 4: Manual test**

Run `sleuth eval eval/sample_repo.yaml`-style golden file's path through the "Run
eval" input against a real indexed repo, confirm status flips `running` → `complete`
within the poll interval, stat tiles populate, and the run appears in the Runs list.
Confirm the bar chart renders a single Voyage bar per metric (no fabricated second
provider).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/EvalScreen.jsx web/src/components/EvalBarChart.jsx web/src/api.js
git commit -m "feat: add Eval screen wired to real eval runs"
```

---

## Task 12: Polish — error states + README

**Files:**
- Modify: `web/src/components/RepoList.jsx` (surface `error_message` prominently for `failed`)
- Modify: `web/src/components/ChatSidebar.jsx` / `ChatScreen.jsx` (disable/tooltip repos that aren't ready — already filtered to `ready` only in Task 10, so this task adds an explicit tooltip on the picker rather than a silent absence)
- Create/Update: top-level `README.md`

**Interfaces:**
- Consumes: nothing new.

- [ ] **Step 1: Add failed-repo error banner to `RepoList.jsx`**

Extend the card's status column: when `repo.status === 'failed'`, render the
`error_message` inline (not just in the tooltip-less detail line already added in
Task 8) — e.g. a small red-tinted banner under the card row using
`var(--status-neutral)` text on a `var(--status-neutral-wash)` background, so a
failure reads as a distinct visual state, not just muted text.

- [ ] **Step 2: Deliberately index a bad URL**

Manual test: submit `https://github.com/does-not-exist/nope-12345` via Add Repo,
confirm the card settles on `failed` with the clone error message visible, not a
spinner stuck on "indexing…" forever.

- [ ] **Step 3: Write `README.md`**

```markdown
# Sleuth

RAG chatbot over GitHub repos. See `CLAUDE.md` for the full project context.

## Running locally

Backend (from repo root, with `.venv` activated and `.env` populated per `.env.example`):

    uvicorn sleuth.api.main:app --reload

Frontend:

    cd web
    cp .env.example .env
    npm install
    npm run dev

Open http://localhost:5173. The API runs on http://localhost:8000.

## CLI

    python -m sleuth add <github_url>
    python -m sleuth list
    python -m sleuth ask <repo_id> "<question>"
    python -m sleuth agentic <path> "<question>"
    python -m sleuth eval <golden_yaml_path>
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/RepoList.jsx README.md
git commit -m "feat: polish error states and add README"
```

---

## Self-Review Notes

- **Spec coverage:** all five design screens (Landing/Repos/Indexing/Chat/Eval) map
  to Tasks 7-11; every design-doc endpoint (`POST /repos`, `GET /repos`,
  `GET /repos/{id}`, `POST /chat`) is covered, plus the extensions the design forced
  (progress endpoint, chat CRUD, eval endpoints) that weren't in the original design
  doc's endpoint list but are required to back the screens faithfully.
- **Known design-to-reality deltas** (all decided with the user on 2026-08-19, not
  silent downgrades): Eval chart shows one provider (Voyage) not two; Indexing shows
  elapsed time not a fabricated ETA; Eval's `@5` label is corrected to the real
  `TOP_K=8`; the Chat screen's accent-color picker isn't shipped; chat history is
  persisted (schema addition) rather than kept ephemeral.
- **Backward compatibility:** `ingest_repo`, `embed_batch`, `stream_answer` all gain
  one optional keyword-only-by-convention parameter each, defaulting to `None` —
  every existing call site (CLI, existing tests) is unaffected. `run_eval`'s return
  type changes from `str` to `EvalSummary`, which *does* break its one existing
  caller (`cli.py`) and its one existing test — both are explicitly updated in
  Task 5, Steps 1 and 4.
