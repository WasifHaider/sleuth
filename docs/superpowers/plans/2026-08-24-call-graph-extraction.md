# Call-Graph Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` "Execution mode" section takes precedence over the sub-skill's default flow — work one full task at a time (not step-by-step with per-step confirmation), explain the concept after each task, log it to `docs/progress.html`, and wait for the user's "okay" before starting the next task. Git commits are done by the user, not Claude — the "Commit" step in each task below documents what *would* be committed; do not run it yourself unless asked.

**Goal:** Add a real (not fabricated) caller→callee call-relationship graph
to the ingest pipeline, so the Indexing Status screen's "Extracting call
relationships and cross-references" step has an actual count behind it.

**Architecture:** New pure module (`sleuth/ingest/call_graph.py`) extracts
call-expression sites from each in-scope chunk via tree-sitter queries and
resolves callee names against a repo-wide leaf-name map built from the
chunks already parsed in this ingest run. Results are stored via new
`call_edges` table + `store.replace_call_edges`, wired into `ingest_repo`
right after chunking, before embedding — no DB round-trip needed, no
changes to embedding/storage logic.

**Tech Stack:** Same as the rest of the ingest pipeline — hand-written
tree-sitter queries (`py-tree-sitter`'s `Query`/`QueryCursor`), raw SQL via
`psycopg`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-24-call-graph-extraction-design.md`

## Global Constraints

- No vendor call-graph/symbol-resolution library — hand-written tree-sitter
  queries only, same as the existing chunker.
- No ORM — raw SQL via `psycopg`, same style as `sleuth/store.py`'s existing
  functions (`upsert_chunks`, `delete_stale_chunks`).
- In scope: Python, JavaScript, TypeScript, JSX, TSX chunks only (the
  `python`/`javascript`/`typescript` keys in `sleuth/ingest/parse.py`'s
  `LANGUAGES`). CSS/HTML/Vue chunks are never scanned as callers.
- Resolution is repo-wide **leaf-name** matching (last segment after `.` of
  `symbol_name`) — no import tracing. Ambiguous matches (multiple chunks
  share a leaf name) produce an edge to every match, not a guess.
- `ingest_repo`'s signature does not change in this plan — no `on_event`
  parameter. That's Plan 2 Task 2's addition, built on top of whatever
  pipeline stages exist when it lands (including this one).
- Every new function gets a test written and watched-fail first (TDD).

---

## Task 1: Call-expression extraction (`extract_call_edges`)

**Files:**
- Create: `sleuth/ingest/queries/calls_python.scm`,
  `sleuth/ingest/queries/calls_javascript.scm`,
  `sleuth/ingest/queries/calls_typescript.scm`, `sleuth/ingest/call_graph.py`
- Test: `tests/test_call_graph.py`

**Interfaces:**
- Consumes: `sleuth.chunking.Chunk` (existing dataclass — `file_path`,
  `symbol_name`, `kind`, `code_text`), `sleuth.ingest.parse.LANGUAGES` (existing
  `dict[str, LanguageSpec]` keyed by file extension, `LanguageSpec` has `.key`
  and `.ts_language`).
- Produces: `sleuth.ingest.call_graph.extract_call_edges(chunks: list[Chunk]) -> list[tuple[str, str | None, str, str | None]]`.
  Each tuple is `(caller_file_path, caller_symbol_name, callee_file_path,
  callee_symbol_name)`. Consumed by Task 3 (pipeline wiring) and matches
  the column order `store.replace_call_edges` (Task 2) expects.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_call_graph.py
from sleuth.chunking import Chunk
from sleuth.ingest.call_graph import extract_call_edges


def test_python_same_file_call_resolves():
    chunks = [
        Chunk("a.py", "foo", "function", 1, 2, "def foo():\n    return bar()"),
        Chunk("a.py", "bar", "function", 4, 5, "def bar():\n    return 1"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.py", "foo", "a.py", "bar") in edges


def test_python_cross_file_call_resolves():
    chunks = [
        Chunk("a.py", "foo", "function", 1, 2, "def foo():\n    return bar()"),
        Chunk("b.py", "bar", "function", 1, 2, "def bar():\n    return 1"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.py", "foo", "b.py", "bar") in edges


def test_python_method_call_via_self_resolves():
    chunks = [
        Chunk("a.py", "Foo.run", "method", 1, 2, "def run(self):\n        return self.helper()"),
        Chunk("a.py", "Foo.helper", "method", 4, 5, "def helper(self):\n        return 1"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.py", "Foo.run", "a.py", "Foo.helper") in edges


def test_unresolved_call_produces_no_edge():
    chunks = [Chunk("a.py", "foo", "function", 1, 2, "def foo():\n    return len([1, 2])")]
    assert extract_call_edges(chunks) == []


def test_ambiguous_leaf_name_produces_edge_to_every_match():
    chunks = [
        Chunk("a.py", "foo", "function", 1, 2, "def foo():\n    return helper()"),
        Chunk("b.py", "helper", "function", 1, 2, "def helper():\n    return 1"),
        Chunk("c.py", "helper", "function", 1, 2, "def helper():\n    return 2"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.py", "foo", "b.py", "helper") in edges
    assert ("a.py", "foo", "c.py", "helper") in edges


def test_duplicate_calls_in_same_chunk_deduplicated():
    chunks = [
        Chunk("a.py", "foo", "function", 1, 3, "def foo():\n    bar()\n    bar()"),
        Chunk("a.py", "bar", "function", 5, 6, "def bar():\n    return 1"),
    ]
    edges = extract_call_edges(chunks)
    assert edges.count(("a.py", "foo", "a.py", "bar")) == 1


def test_javascript_call_resolves():
    chunks = [
        Chunk("a.js", "foo", "function", 1, 1, "function foo() { return bar(); }"),
        Chunk("a.js", "bar", "function", 2, 2, "function bar() { return 1; }"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.js", "foo", "a.js", "bar") in edges


def test_typescript_method_call_via_this_resolves():
    chunks = [
        Chunk("a.ts", "Foo.run", "method", 1, 1, "run() {\n    return this.helper();\n  }"),
        Chunk("a.ts", "Foo.helper", "method", 2, 2, "helper() {\n    return 1;\n  }"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.ts", "Foo.run", "a.ts", "Foo.helper") in edges


def test_javascript_method_shorthand_does_not_spuriously_self_call():
    # Regression test: a bare method chunk `run() { ... }` parsed standalone
    # (not inside a class) mis-parses in tree-sitter's error recovery mode,
    # producing a spurious call_expression for the method's own head. Must
    # not produce a self-referential edge.
    chunks = [
        Chunk("a.js", "Foo.run", "method", 1, 1, "run() {\n    return this.helper();\n  }"),
        Chunk("a.js", "Foo.helper", "method", 2, 2, "helper() {\n    return 1;\n  }"),
    ]
    edges = extract_call_edges(chunks)
    assert ("a.js", "Foo.run", "a.js", "Foo.run") not in edges


def test_css_chunks_never_scanned_as_callers():
    chunks = [Chunk("a.css", "body", "rule", 1, 1, ".body { color: red; }")]
    assert extract_call_edges(chunks) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_call_graph.py -v`
Expected: FAIL — `sleuth.ingest.call_graph` doesn't exist yet.

- [ ] **Step 3: Write the call-expression query files**

```scheme
; sleuth/ingest/queries/calls_python.scm
; Bare call: foo()
(call function: (identifier) @call.name)

; Attribute/method call: self.foo(), obj.foo() — receiver ignored, only the
; rightmost identifier is captured (leaf-name matching, see design doc).
(call function: (attribute attribute: (identifier) @call.name))
```

```scheme
; sleuth/ingest/queries/calls_javascript.scm
; Bare call: foo()
(call_expression function: (identifier) @call.name)

; Member-expression call: this.foo(), obj.foo() — receiver ignored.
(call_expression function: (member_expression property: (property_identifier) @call.name))
```

```scheme
; sleuth/ingest/queries/calls_typescript.scm
; Identical shape to calls_javascript.scm — confirmed empirically that the
; TS and TSX grammars produce the same call_expression/member_expression
; node shapes as plain JS for these patterns (same precedent as
; sleuth/ingest/queries/typescript.scm being shared across .ts/.tsx).
(call_expression function: (identifier) @call.name)
(call_expression function: (member_expression property: (property_identifier) @call.name))
```

- [ ] **Step 4: Write `sleuth/ingest/call_graph.py`**

```python
from functools import lru_cache
from pathlib import Path

from tree_sitter import Language, Parser, Query, QueryCursor

from sleuth.chunking import Chunk
from sleuth.ingest.parse import LANGUAGES

QUERIES_DIR = Path(__file__).parent / "queries"
IN_SCOPE_KEYS = {"python", "javascript", "typescript"}

# JS/TS method chunks are stored as bare shorthand text (`run() { ... }`),
# valid only inside a class body. Parsed standalone, tree-sitter's error
# recovery treats the method's own head as a spurious call_expression.
# Wrapping in a throwaway class avoids it — confirmed empirically (see
# docs/superpowers/specs/2026-08-24-call-graph-extraction-design.md).
# Python needs no such wrapping — a method chunk's stored text is already a
# bare, dedented `def name(self): ...`, valid standalone Python.
_METHOD_WRAP_KEYS = {"javascript", "typescript"}


@lru_cache(maxsize=None)
def _load_call_query(key: str, ts_language: Language) -> Query:
    query_text = (QUERIES_DIR / f"calls_{key}.scm").read_text()
    return Query(ts_language, query_text)


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def _leaf_name(symbol_name: str | None) -> str | None:
    if not symbol_name:
        return None
    return symbol_name.rsplit(".", 1)[-1]


def _extension_of(file_path: str) -> str:
    return "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""


def _callee_names(chunk: Chunk, key: str, ts_language: Language) -> list[str]:
    text = chunk.code_text
    if chunk.kind == "method" and key in _METHOD_WRAP_KEYS:
        text = f"class __SleuthWrapper {{ {text} }}"
    source_bytes = text.encode("utf-8")

    parser = Parser(ts_language)
    tree = parser.parse(source_bytes)
    query = _load_call_query(key, ts_language)

    names = []
    for _, captures in QueryCursor(query).matches(tree.root_node):
        if "call.name" in captures:
            names.append(_node_text(captures["call.name"][0], source_bytes))
    return names


def extract_call_edges(chunks: list[Chunk]) -> list[tuple[str, str | None, str, str | None]]:
    # Repo-wide leaf-name -> chunks map. Built from ALL chunks regardless of
    # language — a callee target can be any chunk, only the caller side is
    # restricted to in-scope languages.
    name_map: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        leaf = _leaf_name(chunk.symbol_name)
        if leaf:
            name_map.setdefault(leaf, []).append(chunk)

    edges: set[tuple[str, str | None, str, str | None]] = set()
    for chunk in chunks:
        spec = LANGUAGES.get(_extension_of(chunk.file_path))
        if spec is None or spec.key not in IN_SCOPE_KEYS:
            continue

        try:
            names = _callee_names(chunk, spec.key, spec.ts_language)
        except Exception:
            continue  # skip chunks that fail to re-parse, don't abort extraction

        for name in names:
            for callee in name_map.get(name, []):
                edges.add((chunk.file_path, chunk.symbol_name, callee.file_path, callee.symbol_name))

    return list(edges)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_call_graph.py -v`
Expected: PASS, all 10 tests green.

- [ ] **Step 6: Commit**

```bash
git add sleuth/ingest/queries/calls_python.scm sleuth/ingest/queries/calls_javascript.scm sleuth/ingest/queries/calls_typescript.scm sleuth/ingest/call_graph.py tests/test_call_graph.py
git commit -m "feat: add call-expression extraction and repo-wide name resolution"
```

---

## Task 2: `call_edges` table + `store.replace_call_edges`

**Files:**
- Modify: `schema.sql` (add `call_edges` table), `sleuth/store.py` (add
  `replace_call_edges`)
- Test: extend `tests/test_store.py`

**Interfaces:**
- Consumes: Task 1's tuple shape — `(caller_file_path, caller_symbol_name,
  callee_file_path, callee_symbol_name)`.
- Produces: `sleuth.store.replace_call_edges(conn, repo_id: str, edges: list[tuple[str, str | None, str, str | None]]) -> int`
  — deletes existing `call_edges` rows for the repo, inserts the given set,
  returns the count inserted. Consumed by Task 3 (pipeline wiring).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_store.py
from sleuth.store import create_repo, replace_call_edges


def test_replace_call_edges_round_trip(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    count = replace_call_edges(
        pg_conn, repo_id,
        [("a.py", "foo", "b.py", "bar"), ("a.py", "foo", "a.py", None)],
    )
    assert count == 2

    rows = pg_conn.execute(
        "SELECT caller_file_path, caller_symbol_name, callee_file_path, callee_symbol_name "
        "FROM call_edges WHERE repo_id = %s ORDER BY callee_file_path",
        (repo_id,),
    ).fetchall()
    assert rows == [("a.py", "foo", "a.py", None), ("a.py", "foo", "b.py", "bar")]


def test_replace_call_edges_clears_previous_run(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    replace_call_edges(pg_conn, repo_id, [("a.py", "foo", "b.py", "bar")])
    replace_call_edges(pg_conn, repo_id, [("a.py", "foo", "c.py", "baz")])

    rows = pg_conn.execute(
        "SELECT callee_file_path FROM call_edges WHERE repo_id = %s", (repo_id,)
    ).fetchall()
    assert rows == [("c.py",)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_store.py -v -k call_edges`
Expected: FAIL — `call_edges` table doesn't exist / `replace_call_edges` not defined.

- [ ] **Step 3: Add the table to `schema.sql`**

Append:

```sql
CREATE TABLE IF NOT EXISTS call_edges (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id             uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    caller_file_path    text NOT NULL,
    caller_symbol_name  text,
    callee_file_path    text NOT NULL,
    callee_symbol_name  text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS call_edges_repo_idx ON call_edges (repo_id);
```

- [ ] **Step 4: Add `replace_call_edges` to `sleuth/store.py`**

```python
def replace_call_edges(
    conn: psycopg.Connection,
    repo_id: str,
    edges: list[tuple[str, str | None, str, str | None]],
) -> int:
    conn.execute("DELETE FROM call_edges WHERE repo_id = %s", (repo_id,))
    for caller_file_path, caller_symbol_name, callee_file_path, callee_symbol_name in edges:
        conn.execute(
            """
            INSERT INTO call_edges
                (repo_id, caller_file_path, caller_symbol_name, callee_file_path, callee_symbol_name)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (repo_id, caller_file_path, caller_symbol_name, callee_file_path, callee_symbol_name),
        )
    return len(edges)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_store.py -v -k call_edges`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add schema.sql sleuth/store.py tests/test_store.py
git commit -m "feat: add call_edges table and replace_call_edges store function"
```

---

## Task 3: Wire into `ingest_repo`

**Files:**
- Modify: `sleuth/ingest/pipeline.py`
- Test: extend `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1's `extract_call_edges(chunks: list[Chunk])`, Task 2's
  `replace_call_edges(conn, repo_id, edges)`.
- Produces: no new public interface — `ingest_repo`'s signature is
  unchanged. This task only changes its internal behavior (real
  `call_edges` rows exist after a run).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline.py

@pytest.fixture
def cross_call_git_repo(tmp_path):
    # Separate fixture from local_git_repo above — that one's a.py/b.py
    # content is asserted on exactly by other tests, so it can't be changed
    # to add a call between them without breaking those assertions.
    repo_dir = tmp_path / "cross_call_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("from b import bar\n\n\ndef foo():\n    return bar()\n")
    (repo_dir / "b.py").write_text("def bar():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_stores_cross_file_call_edges(pg_conn, cross_call_git_repo):
    _mock_voyage()
    config = _config()

    repo_id = await ingest_repo(str(cross_call_git_repo), pg_conn, config)

    rows = pg_conn.execute(
        "SELECT caller_file_path, caller_symbol_name, callee_file_path, callee_symbol_name "
        "FROM call_edges WHERE repo_id = %s",
        (repo_id,),
    ).fetchall()
    assert ("a.py", "foo", "b.py", "bar") in rows
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pipeline.py -v -k call_edges`
Expected: FAIL — no `call_edges` rows exist yet, `ingest_repo` doesn't call
`extract_call_edges`.

- [ ] **Step 3: Wire the new stage into `ingest_repo`**

In `sleuth/ingest/pipeline.py`:

```python
from sleuth.ingest.call_graph import extract_call_edges
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    replace_call_edges,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
)
```

Insert right after the parse/chunk loop that builds `all_chunks`, before
the `current_keys`/`to_embed`/embedding section:

```python
        all_chunks = []
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                continue  # skip files that fail to parse, don't abort the whole index
            all_chunks.extend(chunks)

        call_edges = extract_call_edges(all_chunks)
        replace_call_edges(conn, repo_id, call_edges)
        conn.commit()

        current_keys = {(c.file_path, c.symbol_name) for c in all_chunks}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS, all existing tests in the file still green (no signature
changed, only new internal behavior).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions anywhere else (nothing else calls or depends
on `ingest_repo`'s internals directly).

- [ ] **Step 6: Commit**

```bash
git add sleuth/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire call-graph extraction into ingest_repo"
```

---

## After this plan

Plan 2 Task 2 (indexing progress instrumentation) is next. When it adds
`on_event` to `ingest_repo`, wrap this plan's new stage with:

- `emit("extracting_relationships")` before the `extract_call_edges` call
- `emit("relationships_extracted", relationships=len(call_edges))` after
  `replace_call_edges`

positioned between the existing `"chunked"` and `"embedding_start"` events,
per the design doc's "Progress event integration" section.
