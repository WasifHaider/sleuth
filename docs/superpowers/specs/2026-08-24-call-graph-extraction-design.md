# Call-Graph Extraction — Design

*Date: 2026-08-24. Extends Plan 1's ingest pipeline (`sleuth/ingest/pipeline.py`).
Triggered by the new Indexing Status design (`Sleuth Indexing Status.dc.html`,
see `docs/superpowers/plans/2026-08-19-rag-web-app-fastapi-react.md`), which
shows a step ("Extracting call relationships and cross-references") that the
pipeline did not previously have. This document scopes that step to something
actually feasible for a hand-written, single-repo-owner tool — not the full
accuracy an IDE's "go to definition" would give.*

## Purpose

Add a real (not fabricated) "relationships mapped" count to the indexing
progress screen, backed by an actual caller→callee graph, stored so a future
feature (e.g. "what calls this function") can query it without recomputing.

## Scope

**In scope:** function/method call relationships within Python, JavaScript,
TypeScript, JSX, and TSX chunks — the languages where "function A calls
function B" is a well-defined concept.

**Out of scope:**
- CSS, HTML, Vue — no call-expression semantics in the code sense; these
  languages get no call edges, same as today (no regression, just no new
  feature for them).
- Import statement resolution. A call is matched to a target chunk **by name
  only, repo-wide** — not by tracing which module a name was imported from.
  Tracing imports correctly per-language (relative/absolute paths, aliases,
  re-exports, package resolution) is effectively hand-building a chunk of
  what a language server does, separately per language. Out of proportion to
  what the progress screen needs (a count) and what a future graph feature
  needs (approximate structure, not exact resolution).
- Non-call relationships (inheritance, imports-as-edges, type references).
  Explicitly deferred — call relationships are what the design mockup's
  wording ("call relationships and cross-references") and the recommended
  scope target.

## Resolution strategy: repo-wide name match

A call site's callee is reduced to a single identifier — `foo()` → `foo`,
`self.foo()` → `foo`, `obj.bar()` → `bar` (rightmost identifier, receiver
ignored). That name is looked up against a `leaf_name → [chunk_id]` map built from
every chunk currently stored for the repo (all chunks already carry a
`symbol_name` from the existing chunk data model — Task 3, Plan 1). The map
key is the **leaf** of `symbol_name` — everything after the last `.` — not
the raw field, because methods are stored dotted as `ClassName.method_name`
(existing chunker convention, `query_chunker.py`) while a call site's callee
is always a bare identifier (`self.method_name()` extracts to `method_name`,
never `ClassName.method_name`). Matching on the raw field would silently
resolve zero method calls, defeating the "function/method calls" scope
decision above. Plain functions have no dot, so their leaf name is just
themselves — unaffected.

- **No match** (stdlib call, external library, dynamic dispatch) → no edge.
  Expected to be the majority of call sites in most real repos.
- **Exactly one match** → one edge.
- **Multiple matches** (two files each define a function with the same name)
  → one edge to *every* match. Storing all matches is honest about the
  ambiguity rather than guessing; the alternative (skip, or pick one
  arbitrarily) either throws away real signal or fabricates false certainty.
- Duplicate `(caller, callee)` pairs from multiple call sites in the same
  chunk body are deduplicated before storing/counting — calling the same
  function twice in one function body is one relationship, not two.

This deliberately accepts two known inaccuracies in exchange for staying
within scope: (1) common names produce fan-out edges that aren't all real,
and (2) a name that happens to collide with an unrelated same-named function
in another file can produce a false edge. Both are acceptable given the
alternative (import tracing) is out of proportion to this feature's payoff.

## Pipeline placement

New step in `ingest_repo`, positioned right after the parse/chunk loop
produces `all_chunks` — **before** the embed+upsert step — matching the
mockup's step order (Parsing AST → relationships → Generating embeddings).
Runs identically for CLI (`sleuth add`) and API (`POST /repos`) ingest — one
function, no duplicated logic, consistent with the project's existing
constraint.

This placement is why extraction and storage work off the **in-memory**
`all_chunks: list[Chunk]` the parse/chunk loop already produced, not a
DB re-fetch: at this point in the real pipeline (see
`sleuth/ingest/pipeline.py::ingest_repo`), embedding hasn't run yet, so
`chunks.id` values don't exist for new/changed chunks — only
`upsert_chunks` (which runs later, bundled with embedding, since the
`embedding` column is `NOT NULL`) assigns them. Requiring DB ids would force
extraction to run *after* storage, contradicting the mockup's step order.
See **Storage** below for how this is resolved (natural-key rows instead of
UUID foreign keys).

## Extraction mechanics

For each current chunk, re-parse its `code_text` with tree-sitter (reusing
the existing per-language parser setup from `sleuth/ingest/parse.py`) and
walk for call-expression nodes — `call` in Python's grammar, `call_expression`
in JS/TS/JSX/TSX. This is new *query* work, not new *infrastructure*: same
query-based node walking the chunker itself already does (Task 5's
decorator/export-unwrapping fix set the precedent for query-based,
not manual-recursive, tree walking).

A chunk that fails to re-parse (shouldn't happen — it parsed fine during
initial chunking moments earlier) is skipped, not fatal, mirroring the
chunker's existing skip-on-error behavior for unparseable files.

**Verified quirk, JS/TS method chunks only:** a method chunk's stored
`code_text` is the bare shorthand form, e.g. `run() { return
this.helper(); }` (confirmed against real `chunk_source` output) — valid
*inside* a class body, not as standalone top-level JS/TS. Parsed alone,
tree-sitter's error recovery reads the head `run()` as its own spurious
`call_expression`, which would wrongly resolve to a self-referential edge
if the chunk's own name happens to match another chunk's name (or itself,
since leaf-name matching would find its own leaf). Fix: JS/TS chunks of
`kind == "method"` are wrapped as `f"class __SleuthWrapper {{ {code_text} }}"`
before parsing — confirmed empirically to parse with zero errors and
produce only the real call site. Python needs no such wrapping: a method
chunk's stored text is already a bare `def name(self): ...` (dedented to
column 0), which is valid standalone Python on its own — confirmed against
real `chunk_source` output.

## Storage

New table, `call_edges`, keyed by the **same natural identity** the
`chunks` table already uses for upsert matching (`chunks_identity_idx`:
`repo_id, file_path, symbol_name`) — not a UUID foreign key into
`chunks.id`. This is a direct consequence of the pipeline-placement
decision above: extraction runs before chunk rows have ids, so it stores
what it actually has (file path + symbol name), not an id it doesn't have
yet.

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

Trade-off, stated plainly: a future feature reading this table joins back
to `chunks` on `(repo_id, file_path, symbol_name)` instead of a plain id
lookup, and if a chunk is later renamed/deleted an edge can point at
nothing (a join miss, not a dangling FK error). Acceptable because (a) this
table is fully recomputed every ingest run anyway (see below), so a stale
edge never survives past the next index, and (b) no feature in this task
reads the table — the only consumer today is the progress count.

**Re-index behavior:** every ingest run deletes all `call_edges` rows for the
repo and recomputes from scratch, rather than diffing old vs. new edges.
Justified because extraction has no network/embedding cost — it's a local
tree-sitter pass plus in-memory dict lookups — so unconditional recompute is
cheap, and it avoids the complexity of edge-level diffing for a graph that
can change shape (not just content) between runs. Same "unconditional
re-apply, no incremental-diff framework" philosophy already used for
`apply_schema()` (Plan 1, Task 12 note).

## Progress event integration

**Sequencing note:** this task runs *before* Plan 2 Task 2 (the `on_event`
instrumentation mechanism doesn't exist in `ingest_repo` yet as of this
writing — it's plain, uncallbacked code today). So this task adds the
pipeline *stage* only, with no event-emitting hooks. When Task 2 is built
next, its `on_event` parameter wraps whatever stages exist in `ingest_repo`
at that point — including this one — so the two events below are Task 2's
job to add, not this task's:

- `extracting_relationships` — step starts
- `relationships_extracted` (`relationships=N`) — step ends, `N` is the
  deduplicated edge count actually inserted

Flagging the intended event names now so Task 2's implementer places them
in the right spot (between the existing `"chunked"` and `"embedding_start"`
events) without having to re-derive them.

## Interfaces produced

- `sleuth/ingest/call_graph.py::extract_call_edges(chunks: list[Chunk]) -> list[tuple[str, str | None, str, str | None]]`
  — pure function over the `Chunk` dataclass (`sleuth/chunking.py`) the
  parse/chunk loop already produces in memory. Each returned tuple is
  `(caller_file_path, caller_symbol_name, callee_file_path,
  callee_symbol_name)` — natural-key pairs, not ids (see Storage). Resolves
  a callee name against every chunk's leaf symbol name repo-wide (all
  chunks, any language — a Python function calling something that happens
  to share a name with a CSS chunk is harmless and just won't happen in
  practice). Only chunks whose `file_path` extension maps to an in-scope
  language (python/javascript/typescript keys in `sleuth/ingest/parse.py`'s
  `LANGUAGES`) are scanned as *callers*; every chunk is eligible as a
  *callee* target. Deduplicates identical tuples before returning. Pure and
  DB-free — unit-tested standalone with hand-written `Chunk` lists.
- `sleuth/store.py::replace_call_edges(conn, repo_id, edges: list[tuple[str, str | None, str, str | None]]) -> int`
  — deletes existing `call_edges` rows for the repo, inserts the new set,
  returns count inserted. Raw SQL, same style as existing `upsert_chunks`/
  `delete_stale_chunks`.
- `sleuth/ingest/pipeline.py::ingest_repo` gains one call each to
  `extract_call_edges` + `replace_call_edges`, positioned right after
  `all_chunks` is built and before the embed/upsert step. No signature
  change to `ingest_repo` itself — this is pipeline-internal, not a new
  parameter (the `on_event` parameter is Task 2's addition, not this one's).

## Testing

- `tests/test_call_graph.py` — `extract_call_edges` against known same-file
  and cross-file call patterns per language (Python `def`/method calls,
  JS/TS function/arrow calls, one ambiguous-name case, one no-match/external
  call case that must produce zero edges).
- `tests/test_store.py` — `replace_call_edges` insert + re-run-replaces-old
  round trip.
- `tests/test_pipeline.py` — extend `test_ingest_repo_emits_progress_events`
  (already asserts `"cloned"`/`"ready"` appear in the event list) to also
  assert `"extracting_relationships"` and `"relationships_extracted"` appear,
  and add one new test asserting `call_edges` rows exist after ingesting a
  fixture repo with one known cross-file call.

## Non-goals (explicit)

- No UI for browsing the graph in this task — the design mockup only shows a
  count on the indexing screen. A future "what calls this" feature is
  possible on top of this table but is not built here.
- No retrieval/chat integration — `retrieve/answer.py` does not consult
  `call_edges` in this task.
- No import-resolution accuracy improvement path planned; if repo-wide name
  matching proves too noisy in practice (measured via `sleuth eval` or by
  eyeballing a real repo's edge count), that is its own future task, not a
  silent scope creep here.
