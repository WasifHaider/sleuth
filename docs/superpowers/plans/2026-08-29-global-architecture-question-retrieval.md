# Global/Architecture-Question Retrieval — Plan

*Date: 2026-08-29. Addresses a real limitation found in production use:
SLEUTH's chatbot cannot meaningfully answer broad questions like "rate my
architecture," "summarize the whole project," or "find every place we do X"
— plain top-k vector retrieval has no mechanism for reasoning across an
entire repo, only for finding chunks textually similar to the question. See
`docs/2026-08-29-bugs-and-fixes.md` (bug #18, doc-vs-code retrieval) for a
related but narrower failure mode already fixed.*

## Why this is needed

Indexed retrieval (`sleuth/retrieve/search.py::search_chunks`) embeds the
question and returns the top-k (8 by default) chunks closest by cosine
distance. That's the right tool for "where is X implemented" — it is
structurally the wrong tool for "how good is this architecture" or
"summarize everything," because:

- There is no single chunk that *is* an answer to a whole-repo question.
- top-k is a small fraction of any real codebase; broad questions need
  synthesis across most/all of it, not 8 nearest neighbors.
- No aggregation/rollup layer exists today — every query is one
  retrieve-then-generate pass.

Agentic mode (`sleuth/retrieve/agentic.py`, Task 13) is closer (it can
`grep`/`list_files`/`read_file` iteratively) but is capped at 6 iterations
and has no bird's-eye tool — it can't see repo shape before deciding what to
read.

## Phased plan (cheapest/most useful first)

### Phase 1 — Repo-level summary artifact (biggest bang for the buck)

- During ingest (`sleuth/ingest/pipeline.py`), after chunking, generate a
  hierarchical summary: per-directory summary → per-module summary → one
  repo-level summary, using the existing `Generator` (Groq/NIM).
- Store it as a new `repo_summaries` table (or a special `is_summary` chunk
  flag next to the existing `is_doc` flag) so it's retrievable like any
  other chunk, but it's the one artifact that actually describes the whole
  repo instead of a fragment of it.
- This alone lets "rate my architecture" retrieve something that's actually
  an answer, not an accidental CSS/doc match.

### Phase 2 — Query routing (classify before you retrieve)

- Add a cheap pre-step: one LLM call (or heuristic keyword check —
  "overall," "whole project," "architecture," "rate," "summarize
  everything") classifies the question as **local** (existing top-k vector
  search) vs **global** (needs the Phase 1 summary, or more).
- Local path stays exactly as-is — don't touch what already works.
- Global path retrieves the repo-summary chunk(s) instead of/alongside
  top-k code chunks.

### Phase 3 — Deeper global handling for questions the summary can't cover

- For questions that need something not pre-summarized (e.g. "find every
  place we do X"), extend agentic mode (`sleuth/retrieve/agentic.py`):
  raise the iteration cap for global questions specifically, and add a
  `list_directory_tree` tool so the agent can see repo shape before
  deciding what to read — right now it only has
  `grep`/`list_files`/`read_file`, all local-lookup tools, nothing that
  gives it a bird's-eye view.
- Optional: a batch map-reduce mode — chunk the whole repo into groups,
  summarize each group, reduce into a final answer. Expensive (many LLM
  calls), so this should be an explicit user-triggered "deep analysis"
  action, not the default per-message path.

### Phase 4 — Structural index (stretch, ties into work already sketched)

- Already drafted, unstarted:
  `docs/superpowers/specs/2026-08-24-call-graph-extraction-design.md` +
  `docs/superpowers/plans/2026-08-24-call-graph-extraction.md`.
- A call/import graph lets architecture questions be answered by traversing
  real structural relationships ("what calls what," "what depends on
  what") instead of purely semantic similarity — this is what actually
  makes "architecture" answerable, since architecture is structure, not
  prose similarity.
- As scoped in the existing design doc: function/method call edges only
  (Python/JS/TS/JSX/TSX), resolved by leaf symbol name repo-wide (no import
  tracing), stored in a new `call_edges` table, recomputed fully on every
  re-index. No retrieval/chat integration is built in that phase — it just
  produces and stores the graph; wiring it into `answer.py`/`agentic.py`
  (e.g. a `get_callers`/`get_callees` tool) is explicit follow-up work.

### Phase 5 — Eval coverage

- Add a handful of "global" golden-set questions to Task 14's eval harness
  (`sleuth/eval/runner.py`) so this capability doesn't silently regress
  later.

### Phase 6 — UI signal

- When a question routes to the global path, show something in
  `ChatScreen.jsx` indicating a different/slower mode ran, since it may
  cost more and take longer than a normal local-retrieval answer.

## Recommended order

1 → 2 → 5 (small, cheap, immediately fixes the reported case) → 3 → 4
(bigger investment, only if deep structural Q&A is actually wanted) → 6.

## Status

Not started. This is a planning document only — no code has been written
against it yet. Follow the project's established workflow: one task at a
time, test-driven, confirm with the user before moving to the next phase.
