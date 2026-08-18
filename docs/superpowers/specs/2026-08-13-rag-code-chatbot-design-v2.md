# RAG Code Chatbot ("Sleuth") — Design v2

*Revision date: 2026-08-13. Supersedes 2026-08-06 draft. Changes in this revision:
dual retrieval modes (indexed + agentic/live), pluggable embedding/generation
providers (Voyage/NVIDIA NIM, Groq/NIM), per-repo embedding model tracking, eval
harness as a first-class feature, streaming output, and repositioned headline
features.*

## Purpose

Point the tool at a GitHub repo URL. It clones, parses, and indexes the code. User
then asks natural-language questions about that repo and gets answers grounded in
the actual source (retrieval-augmented generation, not the model guessing from
training data).

Additionally, the tool runs **inside a local project directory** (terminal mode)
and answers questions about that project immediately — no upfront indexing wait —
via agentic retrieval (see Retrieval Modes).

Primary constraint: the user must understand every piece — no black-box library
doing parsing+chunking+embedding+retrieval for them. Tree-sitter, chunking logic,
embedding calls, storage schema, retrieval, the agentic tool loop, and the eval
harness are all hand-written and owned.

## Positioning / Differentiation

This tool does not compete with Claude Code or Cursor as a coding agent. It is a
**codebase Q&A service**. Headline features are the things those tools don't do:

1. **Query any public repo by URL without cloning it** — evaluate a library,
  onboard to an unfamiliar codebase, audit a dependency, all in one command.
2. **Persistent multi-repo knowledge base** — index N repos once (Supabase),
  query any of them from anywhere, including via a shareable web UI used by
   people who never cloned the code.
3. **Measured retrieval quality** — a built-in eval harness (`sleuth eval`)
  produces retrieval hit-rate / MRR / answer-quality numbers. Every design
   choice (chunking granularity, top-k, context headers, embedding provider) is
   justified with numbers in the README, not vibes.
4. **Free-tier, self-hosted, provider-agnostic stack** — Groq + Voyage/NIM +
  Supabase free tiers; generation and embedding models are config values.

Local terminal mode is a secondary convenience, not the pitch.

## Stack Decisions


| Concern        | Choice                                                                                                          | Why                                                                                                                                                                                                                                                        |
| -------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language       | Python (backend/ingestion), React/Vite (frontend)                                                               | tree-sitter Python bindings mature; React chosen despite user having zero prior React experience — plan will build it incrementally with explanation, not scaffold-and-forget                                                                              |
| Parsing        | tree-sitter, per-language grammars                                                                              | produces a real AST per file; needed to chunk by function/class instead of blind text splitting                                                                                                                                                            |
| Chunking       | function/method/class-level via AST walk                                                                        | see rationale below                                                                                                                                                                                                                                        |
| Embeddings     | **Pluggable: Voyage AI (code-oriented, default candidate) or NVIDIA NIM `nemotron-3-embed-1b` (2048-dim)**      | Voyage's code models are code-specialized and likely stronger for this domain; Nemotron is available free via build.nvidia.com. The eval harness decides the default empirically (see Eval Harness). One embedding model per repo, recorded in the schema. |
| Vector store   | Supabase (managed Postgres + pgvector extension)                                                                | plain SQL, pgvector is small/readable, Supabase gives a free-tier hosted Postgres plus a table UI to literally inspect every stored row — fits "own every inch" better than a proprietary vector DB, and is simpler to stand up than self-managed AWS RDS  |
| Generation LLM | **Pluggable: Groq (default, `llama-3.3-70b-versatile`) with NVIDIA NIM as configurable alternative + fallback** | Groq's token speed compounds across the agentic loop's multiple sequential calls; NIM (OpenAI-compatible API) is the fallback on Groq 429s and a swappable alternative. Model + provider are config values.                                                |
| Backend API    | FastAPI                                                                                                         | same language as ingestion pipeline, no cross-language glue                                                                                                                                                                                                |
| Frontend       | React (Vite)                                                                                                    | user's explicit choice; requires extra care in the implementation plan to explain each piece since they're new to it                                                                                                                                       |
| Interface      | Both CLI and web app                                                                                            | CLI runs in indexed mode (remote repos) **and live/agentic mode (current directory)**; web app is the chat UI over indexed repos                                                                                                                           |


AWS (user has free-tier credit) was considered for the vector store but Supabase
covers Postgres+pgvector with less setup; AWS is not used in this spec. Production
hosting of the FastAPI backend / React app is a separate decision, out of scope
here (see Non-Goals).

## Retrieval Modes

One answer pipeline, two retrieval backends behind a common **retriever
interface** (see Components).

### Mode 1 — Indexed (GitHub URL flow)

The original pipeline: clone → parse → chunk → embed → store → pgvector search.
Users expect indexing latency for a remote repo; it's a batch operation.
Incremental re-index via `content_hash` (unchanged).

### Mode 2 — Live/agentic (local terminal flow)

Run `sleuth` inside a project directory. No upfront embedding. The first question
works immediately:

- The LLM is given tools: `grep(pattern, glob?)`, `list_files(glob)`,
`read_file(path, start_line?, end_line?)`.
- It loops: decide what to look at → call tool → read result → repeat, until it
can answer or hits the iteration cap.
- **Loop parameters:** max 6 tool iterations per question; per-call output caps
(grep: first 50 matches; read_file: max 400 lines per call); loop terminates
when the model responds with a final answer instead of a tool call, or on cap,
in which case it answers with what it has and says so.
- This is 2–4 fast LLM round-trips; on Groq this is seconds. The tool loop is
hand-written (~150 lines), consistent with the "own every inch" constraint.

**Optional background index (hybrid):** after launch, the local project can be
indexed in the background using the same content_hash diff logic. Once
`status = ready`, local retrieval becomes hybrid: vector search for semantic
"where is the logic that does X" questions, grep/agentic for exact identifiers.
No file-watcher/daemon: re-hash all files on session start (milliseconds) and
diff against stored hashes.

### Perceived latency

All terminal answers **stream token-by-token** (Groq/NIM streaming APIs).
Time-to-first-token is the felt-speed metric, not total completion time. The web
UI already streams into the chat pane.

## Chunking Rationale

File-level chunking (whole file = one embedding) mixes many unrelated functions
into one vector, so a question about one specific function competes against noise
from the rest of the file and precision drops, especially in large files.

Function/method/class-level chunking (each unit is its own embedding, produced by
walking the tree-sitter AST) means a question about a specific function is compared
directly against that function's own embedding. More rows and embedding calls, but
tree-sitter is designed exactly for this kind of structural split, and retrieval
precision is materially better for "what does X do" style questions.

Fallback: top-level code that isn't inside a function/class (module-level
constants, imports, script bodies) is chunked as its own "module-level" unit per
file.

Each chunk is embedded with a small context header prepended (file path, class
name if any, language) so the embedding and the text given to the LLM both carry
enough context to be useful in isolation.

*(Both the chunking granularity and the context-header decision are validated by
the eval harness, not assumed — see Eval Harness.)*

## Architecture / Data Flow

```
Ingest (indexed mode):
  GitHub URL (or local path for background local index)
    → git clone (shallow)          [skipped for local path]
    → walk repo files, filter by supported extensions
    → tree-sitter parse each file → AST
    → chunk AST into function/method/class/module units
    → prepend context header to each chunk's text
    → hash each chunk's text (content_hash); skip chunks whose hash already
      exists in Supabase for this repo (unchanged since last index) — reuses
      their stored embedding instead of re-embedding
    → Embedder.embed_batch() on remaining (new/changed) chunks, batched and sent
      concurrently (async, bounded concurrency)
    → upsert into Supabase: repos row (status, embedding_model) + chunks rows

Query (indexed mode):
  User question (scoped to one selected repo)
    → embed the question with the SAME model recorded on that repos row
    → pgvector cosine similarity search over chunks WHERE repo_id = X, top-k
    → build prompt: question + retrieved chunk texts (with file/symbol context)
    → Generator.chat() (Groq default, NIM fallback on rate limit)
    → answer streamed to caller (CLI streams tokens / web UI streams into chat)

Query (live/agentic mode):
  User question (scoped to current working directory)
    → tool loop: LLM ⇄ {grep, list_files, read_file} (max 6 iterations)
    → final answer streamed to terminal
    → if background index is ready: hybrid — vector top-k results are offered
      to the loop as an additional `semantic_search(question)` tool
```

## Data Model (Supabase / Postgres)

```
repos
  id              uuid pk
  github_url      text     -- or local path identifier for background-indexed local projects
  status          text     -- pending | indexing | ready | failed
  error_message   text     -- nullable, set when status = failed
  embedding_model text     -- e.g. 'voyage-code-3' | 'nemotron-3-embed-1b'; set at first index
  embedding_dim   int      -- e.g. 1024 | 2048; must match the vector column used
  indexed_at      timestamptz

chunks
  id            uuid pk
  repo_id       uuid fk -> repos.id
  file_path     text
  symbol_name   text     -- function/class name, null for module-level chunks
  kind          text     -- 'function' | 'method' | 'class' | 'module'
  start_line    int
  end_line      int
  code_text     text
  content_hash  text     -- hash of code_text, used to detect unchanged chunks on re-index
  embedding     vector(N)  -- N fixed per deployment/table; see rule below
```

**One embedding model per repo (hard rule).** pgvector columns are fixed-width —
`vector(1024)` and `vector(2048)` are different column types — and mixing
embedders within one collection silently breaks similarity search. Therefore:

- A repo is embedded entirely with one model, recorded in `repos.embedding_model`
and `repos.embedding_dim`.
- Question embeddings at query time always use the model recorded on that repo.
- Chunks tables are per-dimension (e.g. `chunks_1024`, `chunks_2048`), selected
via `repos.embedding_dim`. (Alternative — single table with the max dim and
padding — rejected: padding corrupts cosine similarity semantics.)
- Re-indexing a repo with a *different* embedding model invalidates all its
content_hash skips (embeddings must be regenerated even for unchanged text).

`content_hash` has a unique index scoped to `(repo_id, file_path, symbol_name)` so
re-indexing a repo can look up existing rows by that key, compare hashes, and only
call the embedder for chunks that are new or whose hash changed — unchanged chunks
keep their existing embedding untouched (valid only while `embedding_model` is
unchanged).

Multi-repo support: `repo_id` scopes every chunk and every query. Re-pointing at a
repo creates a new `repos` row; existing indexed repos remain queryable. A repo
picker in the UI lists all `repos` with `status = ready`.

## Provider Interfaces

Two small interfaces keep providers swappable and power the eval comparison. Two
implementations each — no "support every provider" plumbing.

```
Embedder
  embed_batch(texts: list[str]) -> list[vector]
  model_name: str
  dim: int
  # Implementations: VoyageEmbedder, NimEmbedder (nemotron-3-embed-1b, 2048-dim,
  # no `dimensions` truncation support — dim is fixed)

Generator
  chat(messages, tools=None, stream=True) -> token stream / tool calls
  model_name: str
  # Implementations: GroqGenerator (default), NimGenerator (OpenAI-compatible)
  # Fallback chain: Groq → NIM on 429/transient failure (one retry with backoff
  # first, then failover) so rate limits degrade to a slower answer, not an error.
```

Config (env/file): `EMBEDDING_PROVIDER`, `GENERATION_PROVIDER`,
`GENERATION_MODEL`, provider API keys. Both interfaces are plain Python, no
framework dependency.

## Eval Harness (first-class feature)

`sleuth eval` — the project's differentiator and regression suite.

**Input:** a YAML golden set per repo:

```yaml
repo: <github_url or repo_id>
cases:
  - question: "Where is the JWT refresh token validated?"
    expected_files:                # retrieval ground truth (any-of)
      - src/auth/tokens.py
    expected_symbols:              # optional, tightens the check
      - validate_refresh_token
    reference_answer: >            # used by the LLM judge
      Refresh tokens are validated in validate_refresh_token() in
      src/auth/tokens.py, which checks signature, expiry, and revocation
      against the token_blacklist table.
```

**Metrics reported:**

- **Retrieval hit-rate@k** — did any expected file/symbol land in the top-k
retrieved chunks?
- **MRR** — mean reciprocal rank of the first correct chunk.
- **Answer quality** — LLM-as-judge (Generator with a fixed judging prompt)
scores the produced answer against `reference_answer` (1–5).

**Planned experiments (each becomes a README section with numbers):**

1. Function/AST-level vs file-level chunking
2. Context header on vs off
3. top-k = 5 vs 10
4. **Voyage vs Nemotron embeddings** (same repo, same golden set) — decides the
  default embedding provider
5. (later) vector-only vs hybrid BM25+vector

Golden sets live in the repo (`eval/`), ~10–15 cases per benchmark repo. Every
retrieval-affecting change re-runs `sleuth eval` before merge.

## Components

- `ingest/clone.py` — shallow git clone to a working dir, return local path
- `ingest/parse.py` — given a file + its tree-sitter grammar, return AST
- `ingest/chunk.py` — given an AST, walk it and yield chunk dicts (symbol_name, kind, lines, text)
- `ingest/embed.py` — `Embedder` interface + Voyage/NIM implementations; batches sent concurrently (async, bounded concurrency limit), since embedding is network-bound and this is the main lever on total ingest time
- `ingest/pipeline.py` — orchestrates clone → parse → chunk → hash-based skip of unchanged chunks → embed → store for one repo, updates `repos.status` as it progresses; also runs as the background indexer for local mode
- `retrieve/base.py` — **Retriever interface** (`retrieve(question, repo_scope) -> list[Chunk]`); implementations below are swappable
- `retrieve/vector.py` — embed question (with the repo's recorded model), pgvector top-k scoped to repo_id
- `retrieve/agentic.py` — the tool loop: tool definitions (`grep`, `list_files`, `read_file`, and `semantic_search` when an index is ready), loop driver, iteration/output caps, termination rules
- `retrieve/answer.py` — build the prompt from retrieved context, call `Generator` (streaming), return/yield answer
- `llm/generate.py` — `Generator` interface + Groq/NIM implementations + fallback chain
- `eval/runner.py` — `sleuth eval`: load golden YAML, run retrieval + answer per case, compute hit-rate/MRR, run LLM judge, print/report table
- `api/main.py` (FastAPI) — `POST /repos` (add + kick off background indexing), `GET /repos`, `GET /repos/{id}` (status), `POST /chat` (repo_id + question → streamed answer)
- `cli/main.py` — CLI wrapping the same modules: add repo, list repos, ask question (indexed mode); **run in current directory (agentic mode)**; `eval` subcommand
- `web/` (React/Vite) — repo list + add-repo form, indexing status display, chat pane scoped to selected repo

Ingestion, retrieval, generation, and eval modules are plain Python with no
FastAPI or CLI dependency, so the API, the CLI, and the eval runner all call the
same code — no logic duplicated between interfaces.

## Error Handling

- Invalid/private/unreachable repo URL → clone fails → `repos.status = failed`, `error_message` set, surfaced in UI/CLI
- tree-sitter parse failure on a single file → log and skip that file, continue indexing the rest of the repo (one bad file shouldn't fail the whole index)
- Voyage/NIM/Groq API errors (rate limit, network, auth) → retried once with backoff for transient errors; for generation, persistent Groq failure fails over to NIM (fallback chain); persistent failure of the whole chain surfaces as a clear error to the caller, does not crash the backend process
- Chat query against a repo that isn't `status = ready` → rejected with a clear message, not silently run against an empty/partial index
- Agentic loop hits the iteration cap → answers with the context gathered so far and explicitly says the search was cut short, rather than hanging or erroring
- Query-time embedding model mismatch (repo indexed with model A, config now says model B) → question is embedded with the repo's recorded model, not the current config default; a repo can only change models via full re-index

## Testing

- Unit tests for `chunk.py`: given a known small source file, assert the exact expected chunk boundaries (symbol names, kind, line ranges) — this is the core logic the user most needs to trust
- Unit tests for `retrieve/answer.py`'s prompt builder: given fixed chunks + question, assert prompt structure/content
- Unit tests for the agentic loop driver with a **mocked Generator**: assert tool dispatch, output caps, iteration cap, and termination behavior without real API calls
- `sleuth eval` doubles as the retrieval regression suite: any change to chunking/retrieval re-runs eval against the golden sets
- Manual end-to-end test: point the CLI at a small real public repo, confirm it indexes and answers a question correctly, before wiring up the web app; separately, run agentic mode inside a local project and confirm first-question latency with no index

## Non-Goals (out of scope for this spec)

- Production deployment/hosting of the FastAPI backend or React app (AWS EC2/ECS/Amplify/etc.) — decide once the local MVP works
- Auto re-indexing on repo changes (webhooks/polling/**file-watcher daemon**) — re-indexing is manual (re-trigger for an existing repo) or session-start re-hash for local mode; a manual re-index is incremental (skips unchanged chunks via content_hash)
- Multi-language support beyond an initial set (start with Python + JS/TS grammars; adding a language later is additive — new grammar + chunk rules, doesn't change the architecture)
- Auth/multi-user accounts on the web app
- More than two embedding providers or two generation providers — the interfaces make a third additive, but it's not built now
- BM25/hybrid lexical search in indexed mode — the Retriever interface leaves room for it; deferred until the eval harness shows vector-only is the bottleneck

