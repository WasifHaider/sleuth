# SLEUTH

RAG chatbot: point it at a GitHub repo, ask questions about the code, get answers
grounded in the actual source. Core constraint driving every decision below: the
user wants to personally understand every piece — no black-box library doing
parsing/chunking/embedding/retrieval for them. Tree-sitter, chunking logic,
embedding calls, storage schema, retrieval, agentic tool loop, and eval harness
are all hand-written, no vendor SDKs for Voyage/NIM/Groq (raw REST calls via
httpx instead), no ORM (raw SQL via psycopg).

Full design (current): `docs/superpowers/specs/2026-08-13-rag-code-chatbot-design-v2.md`
Superseded design: `docs/superpowers/specs/2026-08-06-rag-code-chatbot-design.md`
Implementation plan (14 tasks, TDD, real code per step): `docs/superpowers/plans/2026-08-06-rag-core-pipeline-cli.md`
— plan since expanded to 14 tasks (agentic mode and eval harness split out as
their own Tasks 13-14) and checked against v2 design for pluggable providers.

This plan is **Plan 1**: core pipeline + CLI only. FastAPI + React web app is a
separate Plan 2, not started, not yet designed in detail.

## Stack (per v2 design)

- Python 3.11+ backend/pipeline, React (Vite) frontend later (Plan 2)
- tree-sitter (function/method/class-level chunking, not whole-file)
- Embeddings: pluggable — Voyage AI (`voyage-code-3`, default candidate) or NVIDIA NIM (`nemotron-3-embed-1b`, 2048-dim); one model per repo, recorded on the `repos` row; eval harness decides the default empirically. Raw REST via httpx.
- Postgres + pgvector for storage — Supabase in prod, local Docker Postgres for dev/tests (same `schema.sql` both places). Chunks tables per embedding dimension (`chunks_1024`, `chunks_2048`) since pgvector columns are fixed-width.
- Generation: pluggable — Groq (default, `llama-3.3-70b-versatile`) with NVIDIA NIM as fallback on 429/transient failure and as a swappable alternative. Raw REST via httpx.
- Two retrieval modes behind one Retriever interface: **indexed** (clone→parse→chunk→embed→pgvector, for remote GitHub URLs) and **live/agentic** (tool loop — `grep`/`list_files`/`read_file`, max 6 iterations — for the current local directory, no upfront indexing wait)
- `sleuth eval` — golden-set YAML per repo, reports retrieval hit-rate@k / MRR / LLM-judge answer quality; first-class regression suite, not an afterthought
- Incremental re-index: chunks hashed (`content_hash`), unchanged chunks skip re-embedding
- Embedding calls sent concurrently (bounded), not sequential
- HTTP calls to Voyage/NIM/Groq retry once on transient failure (429/5xx/network error) via shared `sleuth/http_retry.py`; generation additionally fails over Groq → NIM on persistent failure

## Execution mode (agreed with user, applies for rest of this plan)

Work through the plan's 14 tasks **one full task at a time**, not step-by-step
with per-step confirmation. For each task:

1. Implement all steps of the task (write test → confirm it fails → implement → confirm it passes)
2. After the task is done, explain the concept/why in plain terms
3. Append a new `.task` section to `docs/progress.html` (self-contained local HTML file, opened via `file://`, dark/light aware) summarizing: files touched, what it does, why it's built that way, test results
4. User writes their understanding into `Note.md` (repo root) — **do not write this file yourself**, only read and correct it when asked
5. Wait for the user to say "okay" / confirm before starting the next task

Git commits are done by the user themselves, not by Claude — they explicitly
asked to handle `git add`/`git commit` (and identity setup) manually. Don't commit
on their behalf unless they ask.

## Progress

- **Task 1 (config)**: done. `requirements.txt`, `.env.example`, `.gitignore`, `sleuth/config.py`, `tests/test_config.py`.
- **Task 2 (Postgres schema + connection)**: done. `schema.sql`, `docker-compose.yml`, `sleuth/db.py`, `tests/conftest.py`, `tests/test_db.py`. Container runs on host port **5433** (native WSL Postgres 18 occupies 5432 — see below, do **not** touch it). `get_connection()` registers the pgvector `vector` type on connect, but a fresh DB has no `vector` extension until `apply_schema()` runs — bootstrapped `CREATE EXTENSION IF NOT EXISTS vector`/`pgcrypto` once by hand against the fresh container; persists per volume, one-time step.
- **Task 3 (chunk data model)**: done. `sleuth/chunking.py` (`Chunk` dataclass, `content_hash`, `format_chunk_context`), `tests/test_chunking.py`.
- **Task 4 (tree-sitter parsing)**: done. Language registry + parsing in `sleuth/ingest/`, `tests/test_parse.py`.
- **Task 5 (chunker)**: done, then revisited. Original per-node-type walker missed wrapped nodes (`export_statement`/`decorated_definition` in JS/TS/Python — e.g. `export class Foo {}`, `@app.get(...)`), silently dropping decorated methods and producing wrong line spans for the leftover "junk" chunk. Fixed with a query-based chunker that unwraps decorators/exports before classifying, keeping the outer node's byte span. `tests/test_chunk.py`.
- **Task 6 (git clone / file listing)**: done. `sleuth/ingest/` clone + `list_source_files`, `tests/test_clone.py`.
- **Task 7 (HTTP retry + Voyage embedder)**: done. `sleuth/http_retry.py` (shared retry-once-on-transient-failure helper), Voyage embedder, `tests/test_http_retry.py`, `tests/test_embed.py`.
- **Task 8 (repo/chunk store)**: done. `sleuth/store.py` — raw SQL CRUD, per-dimension table selection (`chunks_1024`/`chunks_2048`), `tests/test_store.py`.
- **Task 9 (ingest pipeline orchestration)**: done. `sleuth/ingest/pipeline.py` — `ingest_repo(github_url, conn, config)` wires clone→chunk→diff-hash→embed→upsert→delete-stale→mark-ready, never raises (failures land in `repos.status = 'failed'`). Built against the actual Voyage-only scope from Tasks 7-8 (see below), not the plan's pluggable-embedder version — calls `VoyageEmbedder` directly, no `get_embedder()` factory, no model-change re-embed path. `tests/test_pipeline.py`.
- **Tasks 7-8 scope note**: plan's v2-design pluggable Voyage/NIM embedder + per-dimension chunk tables (`chunks_1024`/`chunks_2048`) were narrowed down during actual implementation to Voyage-only: one `VoyageEmbedder` class, one `chunks` table with fixed `vector(1024)`, `Config` has no `embedding_provider`/`nim_*` fields. Confirmed with user 2026-08-18 to keep this narrower scope rather than backfilling NIM support now — revisit as its own task if/when NIM is actually needed.
- **Tasks 10-14**: not started. Full suite green: 38/38 passing as of last check.

## Environment notes

- Repo lives at `/mnt/d/Personal/SLEUTH` in WSL (`D:\Personal\SLEUTH` on Windows / `/d/Personal/SLEUTH` in Git Bash).
- Docker is run from **Git Bash on Windows**, not from inside WSL (no WSL Docker integration enabled). Any `docker compose` command should be given to the user to run in Git Bash, not executed directly in this WSL session.
- WSL2 forwards Windows `localhost` ports automatically, so containers started from Windows-side Docker are reachable at `localhost:<port>` from WSL — except where a native WSL-local service is already bound to that same port (see Task 2 note above).
- Python venv at `.venv/` (created via `python3 -m venv .venv`), deps installed from `requirements.txt`.
- Global git config on this machine has an unrelated bug (`git config --global --list` fails with `fatal: cannot chdir to 'D:/'`) even with no `~/.gitconfig` present — worked around by using local (per-repo) git config only. Not investigated further, not blocking.
- `docs/progress.html` — self-contained build log, one section per completed task, meant to be opened directly in a browser.
- `Note.md` (repo root) — user's own understanding notes, written by them after each task, reviewed/corrected by Claude on request.
