# Global/Architecture-Question Retrieval — Phase 1+2+5 Implementation Plan

> **For Hermes:** Follow SLEUTH's established workflow (CLAUDE.md): one task
> at a time, TDD (failing test → implement → passing test), explain the
> concept after each task, append a `.task` section to `docs/progress.html`,
> then STOP and wait for the user's "okay" before starting the next task.
> User writes `Note.md` themselves — never write it. User runs `git
> add`/`git commit` themselves — never commit on their behalf.

**Goal:** Make SLEUTH's chatbot able to meaningfully answer broad questions
("rate my architecture", "summarize the whole project") by giving indexed
retrieval one artifact that actually describes the whole repo, and routing
architecture-flavored questions to use it.

**Source plan:** `docs/superpowers/plans/2026-08-29-global-architecture-question-retrieval.md`
(6 phases, cheapest/most useful first). This document implements the
recommended first slice — **Phase 1 (repo summary) → Phase 2 (query
routing) → Phase 5 (eval coverage)** — and stops there. Phases 3/4/6
(deeper agentic global handling, call-graph structural index, UI mode
indicator) are explicitly out of scope for this plan; they stay as separate
future work in the source plan.

**Architecture:** During ingest, after chunking, generate one repo-level
summary from a cheap "repo map" (file/symbol/kind listing — no full source
text, keeps the prompt small regardless of repo size) via the existing
pluggable `Generator` (Groq/NIM), and persist it in a new `repo_summaries`
table (one row per repo, not shoehorned into the `chunks` table since it
has no code span/embedding-search role). A cheap keyword classifier
(`classify_question`) tags each incoming question `local` vs `global`
before retrieval; `stream_answer` stays exactly as-is for `local` and, for
`global`, prepends the stored summary as a labeled block ahead of the
normal top-k code excerpts — no schema/API/frontend changes needed for the
UI to keep working unmodified. A hierarchical hidden per-directory /
per-module summarization pass (the source plan's original wording) is
deliberately **not** built yet — see "Deliberate simplification" below —
because a single "repo map" prompt covers the actual reported failure case
(architecture questions on typically-sized repos) without adding several
more LLM calls and a map-reduce step; that becomes Phase 1b, added only if
a repo's file/symbol listing itself grows too large for one prompt.

**Tech Stack:** Same as the rest of SLEUTH — raw SQL via psycopg
(`sleuth/store.py`), the existing `Generator` ABC (`sleuth/llm/generate.py`,
Groq primary / NIM fallback, no new provider), pytest + `respx` for HTTP
mocking, no new dependencies.

---

## Current context (from reading the code)

- `sleuth/ingest/pipeline.py::_run_ingest_steps` — after `all_chunks` is
  built (chunking done) and before/after the embed step, is the ingest
  step this plan hooks into. `ingest_repo`'s outer wrapper already catches
  every exception from the whole body and marks the repo `failed` — a
  summarization failure must NOT be allowed to fail the entire index (an
  LLM hiccup shouldn't block local-search chat from working), so the new
  summarization call needs its own inner try/except that just skips
  storing a summary on failure, not re-raise.
- `sleuth/llm/generate.py::get_generator(config)` already returns a ready
  `Generator` (Groq primary) — reuse it directly, no new provider code.
- `sleuth/retrieve/answer.py::stream_answer` is the one place both the CLI
  (`sleuth ask`) and the API (`POST /chat`) funnel through — routing logic
  belongs here, once, not duplicated in both callers.
- `sleuth/store.py` is the only place raw SQL lives — new summary
  persistence functions go here, matching every existing CRUD helper's
  shape (`conn.execute(...)`, no ORM).
- `schema.sql` — `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` /
  `CREATE TABLE IF NOT EXISTS` is the established idempotent-migration
  pattern (see `is_doc`, `user_id` additions) — follow it exactly, since
  `apply_schema()` re-runs this file on every single CLI/API invocation.
- `tests/conftest.py` provides a `pg_conn` fixture (real local Docker
  Postgres, port 5433) — every existing test in `tests/test_pipeline.py`,
  `tests/test_store.py`, `tests/test_answer.py` uses it; new tests follow
  the same fixture.
- `tests/test_pipeline.py::_mock_voyage()` / `@respx.mock` is the
  established pattern for stubbing Voyage; the equivalent for stubbing
  Groq/NIM generation lives in `tests/test_generate.py` /
  `tests/test_answer.py` — reuse that mocking style for the new
  summarization tests instead of inventing a new one.

## Deliberate simplification vs the source plan's Phase 1 wording

The source plan says "hierarchical summary: per-directory → per-module →
repo-level, using the existing Generator." This plan builds the
**repo-level summary only, from a metadata listing (not source text)** in
one LLM call, because:

- The actual reported failure (`docs/superpowers/plans/2026-08-29-...md`,
  "Why this is needed") is that NO artifact today describes the whole
  repo — going from zero to one summary already fixes the reported case.
- Per-directory/per-module intermediate summaries only earn their cost
  (N extra LLM calls per ingest) once a repo is too large for a single
  "list every file/symbol/kind" prompt to fit in context. That listing is
  cheap text (a few hundred bytes per chunk row, not full source), so a
  many-thousand-chunk repo would need to hit real limits before this
  matters.
- Keeping it flat here means fewer moving parts to review/test now, and a
  clean seam to add the hierarchical map-reduce later (Phase 1b) without
  touching the storage schema or the Phase 2 routing code at all — only
  `summarize_repo`'s internals would change.

State this decision to the user when Task 2 is presented (per the
project's per-task "explain the concept" convention) so they can veto it
before more code is built on top.

---

## Task 1: `repo_summaries` table

**Objective:** Add durable storage for one summary per repo.

**Files:**
- Modify: `schema.sql` (append at end, following the existing idempotent
  `CREATE TABLE IF NOT EXISTS` pattern)

**Step 1: Add the table**

```sql
-- Global/architecture-question retrieval (2026-08-30): a single row per
-- repo holding a cheap LLM-generated "what is this repo" summary, built
-- from a repo-map of file/symbol/kind (not full source text) during
-- ingest. Not modeled as a chunks row (no code span, no vector search
-- role — it's read back whole, not retrieved by cosine distance) and not
-- an ALTER on chunks, since a summary isn't "a piece of the codebase" the
-- way a chunk is. One-to-one with repos: overwritten wholesale on every
-- re-index (see upsert_repo_summary), never versioned/append-only.
CREATE TABLE IF NOT EXISTS repo_summaries (
    repo_id      uuid PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
    summary      text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now()
);
```

**Step 2: Verify schema applies cleanly**

Run: `docker compose exec -T postgres psql -U postgres -d sleuth -f -  < schema.sql`
(or however the project already applies it against the local dev DB — check
`docker-compose.yml` / existing `apply_schema()` call path). Since this is a
Docker command, hand it to the user to run in Git Bash on Windows per
CLAUDE.md's environment note — do not run `docker compose` from this WSL
session directly. Actual application in tests happens automatically:
`tests/conftest.py`'s `pg_conn` fixture calls `apply_schema()` before each
test, so Step 3 below is the real verification.

**Step 3: Confirm via a throwaway query in a test session**

No standalone test needed for a bare `CREATE TABLE` — Task 2's store tests
exercise it directly. Skip straight to Task 2.

---

## Task 2: `store.py` — `upsert_repo_summary` / `get_repo_summary`

**Objective:** CRUD helpers for the new table, matching the file's existing
style (plain functions, `conn.execute`, no transaction management —
callers commit).

**Files:**
- Modify: `sleuth/store.py` (append near the other repo-scoped helpers,
  e.g. after `set_repo_embedding_info`)
- Test: `tests/test_store.py`

**Step 1: Write failing tests**

```python
def test_upsert_repo_summary_then_get_returns_it(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/x/y")
    pg_conn.commit()

    upsert_repo_summary(pg_conn, repo_id, "This repo is a RAG chatbot.")
    pg_conn.commit()

    assert get_repo_summary(pg_conn, repo_id) == "This repo is a RAG chatbot."


def test_upsert_repo_summary_overwrites_on_reindex(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/x/y")
    pg_conn.commit()

    upsert_repo_summary(pg_conn, repo_id, "first summary")
    pg_conn.commit()
    upsert_repo_summary(pg_conn, repo_id, "second summary")
    pg_conn.commit()

    assert get_repo_summary(pg_conn, repo_id) == "second summary"


def test_get_repo_summary_returns_none_when_absent(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/x/y")
    pg_conn.commit()

    assert get_repo_summary(pg_conn, repo_id) is None
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_store.py -k repo_summary -v`
Expected: FAIL — `ImportError`/`AttributeError`, `upsert_repo_summary` not
defined.

**Step 3: Implement**

```python
def upsert_repo_summary(conn: psycopg.Connection, repo_id: str, summary: str) -> None:
    conn.execute(
        """
        INSERT INTO repo_summaries (repo_id, summary, generated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (repo_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            generated_at = EXCLUDED.generated_at
        """,
        (repo_id, summary),
    )


def get_repo_summary(conn: psycopg.Connection, repo_id: str) -> str | None:
    row = conn.execute(
        "SELECT summary FROM repo_summaries WHERE repo_id = %s", (repo_id,)
    ).fetchone()
    return row[0] if row is not None else None
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_store.py -k repo_summary -v`
Expected: 3 passed

**Step 5: Commit**

Leave to the user (per CLAUDE.md, they handle `git add`/`git commit`
themselves) — just report the diff is ready.

---

## Task 3: `sleuth/summarize.py` — repo-map summary generation

**Objective:** Build a single LLM prompt from chunk metadata (file/kind/
symbol only, not code bodies) and get back a repo-level summary string.

**Files:**
- Create: `sleuth/summarize.py`
- Test: `tests/test_summarize.py`

**Step 1: Write failing tests**

```python
import pytest

from sleuth.chunking import Chunk
from sleuth.summarize import build_repo_map, summarize_repo

SUMMARY_SYSTEM_PROMPT_MARKER = "architecture"  # sanity check the prompt asks for this


def _chunk(file_path, symbol_name, kind="function"):
    return Chunk(file_path=file_path, symbol_name=symbol_name, kind=kind,
                 start_line=1, end_line=2, code_text="pass")


def test_build_repo_map_lists_every_file_and_symbol_once():
    chunks = [
        _chunk("sleuth/store.py", "create_repo"),
        _chunk("sleuth/store.py", "get_repo"),
        _chunk("sleuth/cli.py", None, kind="module"),
    ]

    repo_map = build_repo_map(chunks)

    assert "sleuth/store.py" in repo_map
    assert "create_repo" in repo_map
    assert "get_repo" in repo_map
    assert "sleuth/cli.py" in repo_map


def test_build_repo_map_excludes_doc_chunks():
    chunks = [
        _chunk("sleuth/store.py", "create_repo"),
        _chunk("docs/architecture.html", None, kind="module"),
    ]

    repo_map = build_repo_map(chunks)

    assert "sleuth/store.py" in repo_map
    assert "docs/architecture.html" not in repo_map


class _FakeGenerator:
    def __init__(self, response):
        self.response = response
        self.received_messages = None

    async def chat(self, messages, stream=True):
        self.received_messages = messages
        yield self.response


@pytest.mark.asyncio
async def test_summarize_repo_calls_generator_with_repo_map_and_returns_text():
    chunks = [_chunk("sleuth/store.py", "create_repo")]
    generator = _FakeGenerator("This is a RAG chatbot backend.")

    summary = await summarize_repo(chunks, generator)

    assert summary == "This is a RAG chatbot backend."
    assert "sleuth/store.py" in generator.received_messages[-1]["content"]


@pytest.mark.asyncio
async def test_summarize_repo_returns_none_for_empty_chunk_list():
    generator = _FakeGenerator("unused")

    summary = await summarize_repo([], generator)

    assert summary is None
    assert generator.received_messages is None  # never called
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_summarize.py -v`
Expected: FAIL — `ModuleNotFoundError: sleuth.summarize`

**Step 3: Implement**

```python
"""Repo-level architecture summary generation (Phase 1 of the
global/architecture-question retrieval plan, see
docs/superpowers/plans/2026-08-29-global-architecture-question-retrieval.md).

Deliberately built from a "repo map" — file path / kind / symbol name for
every non-doc chunk — instead of full source text or a hierarchical
per-directory pass. Keeps the prompt small regardless of repo size and
needs exactly one LLM call per ingest. See the Phase 1+2+5 implementation
plan's "Deliberate simplification" section for why, and when this should
become a real map-reduce (Phase 1b) instead.
"""

SUMMARY_SYSTEM_PROMPT = (
    "You are analyzing a codebase's file/symbol listing (not the source "
    "code itself). Write a concise architecture summary: what the project "
    "is, its major components/modules and what each does, and how they "
    "likely fit together. Base this only on the file paths, symbol names, "
    "and kinds given — do not invent implementation details you can't see. "
    "3-6 short paragraphs, no preamble."
)


def build_repo_map(chunks) -> str:
    lines = []
    for c in chunks:
        if c.is_doc:
            continue
        symbol = c.symbol_name or "(module level)"
        lines.append(f"{c.file_path}: {c.kind} {symbol}")
    return "\n".join(lines)


async def summarize_repo(chunks, generator) -> str | None:
    if not chunks:
        return None
    repo_map = build_repo_map(chunks)
    if not repo_map:
        return None  # every chunk was a doc chunk — nothing to summarize
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": repo_map},
    ]
    return "".join([token async for token in generator.chat(messages, stream=False)])
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_summarize.py -v`
Expected: 5 passed

**Step 5: Commit** — leave to user.

---

## Task 4: Wire summarization into the ingest pipeline

**Objective:** After chunking, generate and store the repo summary as a
non-fatal step (failure here must not fail the whole index).

**Files:**
- Modify: `sleuth/ingest/pipeline.py`
- Test: `tests/test_pipeline.py`

**Step 1: Write failing test**

```python
# tests/test_pipeline.py — add near the other ingest_repo tests

def _mock_groq(response_text="This repo does X."):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": response_text}}]},
        )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=handler)


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_stores_repo_summary(pg_conn, local_git_repo):
    _mock_voyage()
    _mock_groq("This repo has foo() and bar().")
    config = _config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)

    from sleuth.store import get_repo_summary
    assert get_repo_summary(pg_conn, repo_id) == "This repo has foo() and bar()."


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_still_marks_ready_when_summarization_fails(pg_conn, local_git_repo):
    _mock_voyage()
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("boom")
    )
    config = _config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)

    row = pg_conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "ready"  # summarization failure must not fail the whole index

    from sleuth.store import get_repo_summary
    assert get_repo_summary(pg_conn, repo_id) is None
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_pipeline.py -k repo_summary -v`
Expected: FAIL — `test_ingest_repo_stores_repo_summary` gets `None` back
(nothing wired yet); the failure-tolerance test currently would actually
also fail differently since Groq isn't called at all yet — both should
fail for "not implemented" reasons, confirm the failure messages make
sense before moving on.

**Step 3: Implement** — in `sleuth/ingest/pipeline.py`:

```python
from sleuth.llm.generate import get_generator
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
    upsert_repo_summary,
)
from sleuth.summarize import summarize_repo
```

In `_run_ingest_steps`, after the `emit("chunked", chunks=len(all_chunks))`
line and before the embedding block, add:

```python
        # Summarization failure is deliberately non-fatal: a flaky/rate-
        # limited Groq call here must not block local-search chat (the
        # thing that actually works today) from ever becoming available.
        # ingest_repo's outer wrapper would otherwise catch this and mark
        # the WHOLE repo failed over what's really an optional add-on.
        try:
            generator = get_generator(config)
            summary = await summarize_repo(all_chunks, generator)
            if summary:
                upsert_repo_summary(conn, repo_id, summary)
                conn.commit()
                emit("summarized")
        except Exception as exc:
            emit("summary_failed", error=str(exc))
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: all pass, including the two new ones (full file, not just `-k`,
to confirm nothing else broke).

**Step 5: Commit** — leave to user.

---

## Task 5: `sleuth/retrieve/routing.py` — cheap local/global classifier

**Objective:** A pure heuristic (no LLM call — keeps this fast/free)
classifying a question as `"local"` or `"global"`.

**Files:**
- Create: `sleuth/retrieve/routing.py`
- Test: `tests/test_routing.py`

**Step 1: Write failing tests**

```python
from sleuth.retrieve.routing import classify_question


def test_classifies_architecture_question_as_global():
    assert classify_question("Rate my architecture") == "global"


def test_classifies_summarize_whole_project_as_global():
    assert classify_question("Can you summarize the whole project?") == "global"


def test_classifies_specific_function_question_as_local():
    assert classify_question("Where is create_repo implemented?") == "local"


def test_classifies_find_every_place_as_local_not_covered_by_summary():
    # "find every place we do X" needs Phase 3 (agentic global mode), not
    # the Phase 1 summary — the source plan explicitly calls this out as
    # NOT what the summary artifact covers, so it must stay local for now.
    assert classify_question("Find every place we call requests.get") == "local"


def test_is_case_insensitive():
    assert classify_question("WHAT IS THE OVERALL ARCHITECTURE?") == "global"
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_routing.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement**

```python
"""Cheap keyword-based question routing (Phase 2 of the global/architecture
retrieval plan). No LLM call by design — a heuristic false positive just
means a normal question gets the summary prepended too, which is harmless;
a false negative just means a broad question falls back to plain top-k
search, today's existing behavior. Either failure mode is safe, so a fast
keyword check beats spending an extra LLM round-trip on every question to
classify it."""

import re

_GLOBAL_PATTERNS = [
    r"\barchitecture\b",
    r"\boverall\b",
    r"\bwhole (project|repo|repository|codebase)\b",
    r"\bentire (project|repo|repository|codebase)\b",
    r"\bsummarize (the )?(whole|everything|this repo|this project)\b",
    r"\brate (my|this) (architecture|codebase|project|design)\b",
    r"\bhigh.level (overview|summary|design)\b",
    r"\bwhat does this (project|repo|codebase) do\b",
]
_GLOBAL_RE = re.compile("|".join(_GLOBAL_PATTERNS), re.IGNORECASE)


def classify_question(question: str) -> str:
    return "global" if _GLOBAL_RE.search(question) else "local"
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_routing.py -v`
Expected: 5 passed

**Step 5: Commit** — leave to user.

---

## Task 6: Wire routing into `stream_answer`

**Objective:** Global questions get the stored summary prepended to the
prompt, ahead of the normal top-k code excerpts. Local path is untouched.

**Files:**
- Modify: `sleuth/retrieve/answer.py`
- Test: `tests/test_answer.py`

**Step 1: Write failing tests**

```python
# tests/test_answer.py — add

@pytest.mark.asyncio
async def test_build_prompt_prepends_summary_for_global_questions(pg_conn):
    # build_prompt gains an optional summary param; when present it's
    # prepended, labeled, ahead of the excerpt blocks.
    prompt = build_prompt("Rate my architecture", [], summary="This is a RAG chatbot.")
    assert prompt.index("This is a RAG chatbot.") < prompt.index("Relevant excerpts")
    assert "REPO SUMMARY" in prompt


def test_build_prompt_omits_summary_block_when_none():
    prompt = build_prompt("Where is X?", [])
    assert "REPO SUMMARY" not in prompt


@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_includes_stored_summary_for_global_question(pg_conn, ...):
    # Arrange: a ready repo with a stored repo_summary row + at least one
    # chunk row, mock Voyage embed + Groq chat, assert the request body
    # sent to Groq contains the summary text when question matches
    # classify_question == "global".
    ...
```

(The exact fixture wiring for `test_stream_answer_includes_stored_summary_...`
should mirror whatever existing `pg_conn`-based fixture
`test_stream_answer_*` tests already use in `tests/test_answer.py` — read
that file's existing tests first, since it wasn't included in this plan's
research pass; match its conventions instead of inventing a new pattern.)

**Step 2: Run to verify failure**

Run: `pytest tests/test_answer.py -v`
Expected: FAIL — `build_prompt() got an unexpected keyword argument 'summary'`

**Step 3: Implement** — in `sleuth/retrieve/answer.py`:

```python
from sleuth.retrieve.routing import classify_question
from sleuth.store import get_repo_summary


def build_prompt(question: str, results: list[SearchResult], summary: str | None = None) -> str:
    blocks = []
    for r in results:
        symbol = r.symbol_name or "(module level)"
        label = "DOCUMENTATION" if r.is_doc else "CODE"
        blocks.append(
            f"# [{label}] File: {r.file_path}\n# {r.kind}: {symbol} (lines {r.start_line}-{r.end_line})\n\n{r.code_text}"
        )
    context = "\n\n---\n\n".join(blocks)
    prompt = f"Question: {question}\n\n"
    if summary:
        prompt += (
            "REPO SUMMARY (architecture-level overview, generated from the "
            "repo's file/symbol listing — use this for broad/whole-repo "
            "questions, and still ground specific implementation claims in "
            "the excerpts below):\n\n" + summary + "\n\n---\n\n"
        )
    prompt += f"Relevant excerpts:\n\n{context}"
    return prompt
```

In `stream_answer`, after `results = search_chunks(...)`:

```python
    summary = None
    if classify_question(question) == "global":
        summary = get_repo_summary(conn, repo_id)
    prompt = build_prompt(question, results, summary=summary)
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_answer.py -v`
Expected: all pass.

**Step 5: Commit** — leave to user.

---

## Task 7: Full suite regression check

**Objective:** Confirm nothing in the existing 127+ tests broke.

**Step 1:** Run: `pytest` (from WSL, `.venv/bin/activate` first per the
Environment notes in memory/CLAUDE.md)
Expected: previous pass count + the ~13 new tests added above, all green.

**Step 2:** If `test_api_auth.py`/`test_api_repos.py`/`test_api_chat.py`
still show the known pre-existing `TestClient` lifespan failures (see
memory), confirm they're the same known failures, not new ones — don't
treat them as regressions from this work.

---

## Task 8 (Phase 5): Eval coverage for global questions

**Objective:** Add golden-set cases so this capability has regression
coverage in the existing eval harness, per source plan Phase 5.

**Files:**
- Modify: `eval/sample_repo.yaml` (template — add one commented example
  case showing the pattern for a real repo owner to copy)
- Modify: `tests/fixtures/sample_golden.yaml`
- Test: `tests/test_eval_runner.py`

**Step 1: Read the existing fixture first**

Read `tests/fixtures/sample_golden.yaml` and `eval/sample_repo.yaml` in
full before editing — this plan doesn't reproduce their current contents,
match their existing structure exactly (same `repo`/`cases` keys,
`GoldenCase` fields from `sleuth/eval/runner.py`).

**Step 2: Write failing test**

```python
# tests/test_eval_runner.py — add
@pytest.mark.asyncio
@respx.mock
async def test_run_eval_handles_global_architecture_question(pg_conn, ...):
    # Uses a golden case whose question triggers classify_question ==
    # "global" (e.g. "What is the overall architecture of this repo?").
    # Since run_eval calls build_prompt directly today (not stream_answer),
    # confirm it either (a) also routes through get_repo_summary once
    # Task 9 below wires it in, or (b) document explicitly that eval
    # intentionally stays local-only for now if wiring it in is out of
    # scope — decide this explicitly with the user before writing the
    # test, since sleuth/eval/runner.py currently duplicates build_prompt
    # call site logic rather than calling stream_answer.
    ...
```

**Step 3: Decide scope with the user before implementing**

`sleuth/eval/runner.py::run_eval` builds its own prompt via
`build_prompt(case.question, search_results)` directly — it does not call
`stream_answer`, so Task 6's routing wiring does not automatically apply
here. Ask the user: should `run_eval` also route global questions through
`get_repo_summary`/`classify_question` (duplicate ~3 lines from
`stream_answer`, keep the harness scoring the real behavior), or is a
golden-set case just meant to catch a *plain* local-retrieval regression
on an architecture-sounding question rather than actually exercise the new
global path? This determines whether Task 8's implementation touches
`runner.py` at all. Don't guess — this is exactly the kind of scope
question the project's execution mode expects to surface, not silently
resolve.

**Step 4 (once scope confirmed): implement, run, commit** following the
same TDD steps as every task above.

---

## Files touched (summary)

- `schema.sql` — new `repo_summaries` table
- `sleuth/store.py` — `upsert_repo_summary`, `get_repo_summary`
- `sleuth/summarize.py` — new file, `build_repo_map`, `summarize_repo`
- `sleuth/ingest/pipeline.py` — non-fatal summarization step after chunking
- `sleuth/retrieve/routing.py` — new file, `classify_question`
- `sleuth/retrieve/answer.py` — `build_prompt` gains `summary` param,
  `stream_answer` routes global questions through `get_repo_summary`
- `sleuth/eval/runner.py` — maybe, pending Task 8 scope decision
- `tests/test_store.py`, `tests/test_summarize.py` (new),
  `tests/test_pipeline.py`, `tests/test_routing.py` (new),
  `tests/test_answer.py`, `tests/test_eval_runner.py`,
  `tests/fixtures/sample_golden.yaml`, `eval/sample_repo.yaml`
- `docs/progress.html` — one `.task` section per task, per CLAUDE.md

## Explicitly not in this plan

- Phase 3 (agentic global mode, `list_directory_tree` tool, batch
  map-reduce "deep analysis" action)
- Phase 4 (call-graph structural index — already has its own draft
  design/plan docs, untouched by this work)
- Phase 6 (UI signal that a different/slower mode ran) — Task 6 above
  keeps the API/frontend contract byte-identical on purpose so this stays
  a pure backend change; revisit Phase 6 separately once the user wants
  the UI to actually show global-mode happened.
- Re-embedding/backfilling `repo_summaries` for already-indexed repos —
  same caveat as the existing `is_doc` backfill: a summary only exists
  after a repo's NEXT re-index (Retry indexing / `sleuth add` again),
  existing `ready` repos have `get_repo_summary` return `None` until then,
  and `stream_answer`'s global path already falls back gracefully (just
  no summary block, same as it never being wired at all) when that's the
  case.
