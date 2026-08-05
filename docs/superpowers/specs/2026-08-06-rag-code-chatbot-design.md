# RAG Code Chatbot — Design

## Purpose

Point the tool at a GitHub repo URL. It clones, parses, and indexes the code. User
then asks natural-language questions about that repo and gets answers grounded in
the actual source (retrieval-augmented generation, not the model guessing from
training data).

Primary constraint: the user must understand every piece — no black-box library
doing parsing+chunking+embedding+retrieval for them. Tree-sitter, chunking logic,
embedding calls, storage schema, and retrieval are all hand-written and owned.

## Stack Decisions

| Concern | Choice | Why |
|---|---|---|
| Language | Python (backend/ingestion), React/Vite (frontend) | tree-sitter Python bindings mature; React chosen despite user having zero prior React experience — plan will build it incrementally with explanation, not scaffold-and-forget |
| Parsing | tree-sitter, per-language grammars | produces a real AST per file; needed to chunk by function/class instead of blind text splitting |
| Chunking | function/method/class-level via AST walk | see rationale below |
| Embeddings | Voyage AI (code-oriented embedding model) | already researched by user; good code embedding quality |
| Vector store | Supabase (managed Postgres + pgvector extension) | plain SQL, pgvector is small/readable, Supabase gives a free-tier hosted Postgres plus a table UI to literally inspect every stored row — fits "own every inch" better than a proprietary vector DB, and is simpler to stand up than self-managed AWS RDS |
| Generation LLM | Groq API, default `llama-3.3-70b-versatile` | free/fast inference; model name is one config value, swappable later |
| Backend API | FastAPI | same language as ingestion pipeline, no cross-language glue |
| Frontend | React (Vite) | user's explicit choice; requires extra care in the implementation plan to explain each piece since they're new to it |
| Interface | Both CLI and web app | CLI for quick/direct use and debugging the pipeline; web app for the "proper UI" chat experience |

AWS (user has free-tier credit) was considered for the vector store but Supabase
covers Postgres+pgvector with less setup; AWS is not used in this spec. Production
hosting of the FastAPI backend / React app is a separate decision, out of scope
here (see Non-Goals).

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

## Architecture / Data Flow

```
Ingest:
  GitHub URL
    → git clone (shallow)
    → walk repo files, filter by supported extensions
    → tree-sitter parse each file → AST
    → chunk AST into function/method/class/module units
    → prepend context header to each chunk's text
    → hash each chunk's text (content_hash); skip chunks whose hash already
      exists in Supabase for this repo (unchanged since last index) — reuses
      their stored embedding instead of re-embedding
    → Voyage embed remaining (new/changed) chunks, batched and sent concurrently
      (async, bounded concurrency) instead of one batch at a time
    → upsert into Supabase: repos row (status) + chunks rows (with embedding)

Query:
  User question (scoped to one selected repo)
    → Voyage embed the question
    → pgvector cosine similarity search over chunks WHERE repo_id = X, top-k
    → build prompt: question + retrieved chunk texts (with file/symbol context)
    → Groq chat completion
    → answer returned to caller (CLI prints it / web UI streams it into chat pane)
```

## Data Model (Supabase / Postgres)

```
repos
  id            uuid pk
  github_url    text
  status        text   -- pending | indexing | ready | failed
  error_message text   -- nullable, set when status = failed
  indexed_at    timestamptz

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
  embedding     vector(N)  -- N = Voyage model's output dimension
```

`content_hash` has a unique index scoped to `(repo_id, file_path, symbol_name)` so
re-indexing a repo can look up existing rows by that key, compare hashes, and only
call Voyage for chunks that are new or whose hash changed — unchanged chunks keep
their existing embedding untouched.

Multi-repo support: `repo_id` scopes every chunk and every query. Re-pointing at a
repo creates a new `repos` row; existing indexed repos remain queryable. A repo
picker in the UI lists all `repos` with `status = ready`.

## Components

- `ingest/clone.py` — shallow git clone to a working dir, return local path
- `ingest/parse.py` — given a file + its tree-sitter grammar, return AST
- `ingest/chunk.py` — given an AST, walk it and yield chunk dicts (symbol_name, kind, lines, text)
- `ingest/embed.py` — batch chunk texts through Voyage API; batches are sent concurrently (async, bounded concurrency limit) rather than one at a time, since embedding is network-bound and this is the main lever on total ingest time
- `ingest/pipeline.py` — orchestrates clone → parse → chunk → hash-based skip of unchanged chunks → embed → store for one repo, updates `repos.status` as it progresses
- `retrieve/search.py` — embed a question, run pgvector top-k query scoped to repo_id
- `retrieve/answer.py` — build the prompt from retrieved chunks, call Groq, return answer
- `api/main.py` (FastAPI) — `POST /repos` (add + kick off background indexing), `GET /repos`, `GET /repos/{id}` (status), `POST /chat` (repo_id + question → answer)
- `cli/main.py` — thin CLI wrapping the same ingest/retrieve modules (add repo, list repos, ask question)
- `web/` (React/Vite) — repo list + add-repo form, indexing status display, chat pane scoped to selected repo

Ingestion and retrieval modules are plain Python functions/classes with no FastAPI
or CLI dependency, so both the API and the CLI call the same code — no logic
duplicated between the two interfaces.

## Error Handling

- Invalid/private/unreachable repo URL → clone fails → `repos.status = failed`, `error_message` set, surfaced in UI/CLI
- tree-sitter parse failure on a single file → log and skip that file, continue indexing the rest of the repo (one bad file shouldn't fail the whole index)
- Voyage/Groq API errors (rate limit, network, auth) → retried once with backoff for transient errors; persistent failure surfaces as a clear error to the caller, does not crash the backend process
- Chat query against a repo that isn't `status = ready` → rejected with a clear message, not silently run against an empty/partial index

## Testing

- Unit tests for `chunk.py`: given a known small source file, assert the exact expected chunk boundaries (symbol names, kind, line ranges) — this is the core logic the user most needs to trust
- Unit tests for `retrieve/answer.py`'s prompt builder: given fixed chunks + question, assert prompt structure/content
- Manual end-to-end test: point the CLI at a small real public repo, confirm it indexes and answers a question correctly, before wiring up the web app

## Non-Goals (out of scope for this spec)

- Production deployment/hosting of the FastAPI backend or React app (AWS EC2/ECS/Amplify/etc.) — decide once the local MVP works
- Auto re-indexing on repo changes (webhooks/polling) — re-indexing is manual (re-trigger for an existing repo), though a manual re-index is now incremental (skips unchanged chunks via content_hash, see Data Model / Components)
- Multi-language support beyond an initial set (start with Python + JS/TS grammars; adding a language later is additive — new grammar + chunk rules, doesn't change the architecture)
- Auth/multi-user accounts on the web app
