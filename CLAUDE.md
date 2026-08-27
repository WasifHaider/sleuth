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

Plan 1 (below) is core pipeline + CLI only, **done** (all 14 tasks). Plan 2
(FastAPI + React web app) is now **in progress** — plan doc:
`docs/superpowers/plans/2026-08-19-rag-web-app-fastapi-react.md`. See
"Progress — Plan 2" below.

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
- **Tasks 7-8 scope note**: plan's v2-design pluggable Voyage/NIM embedder + per-dimension chunk tables (`chunks_1024`/`chunks_2048`) were narrowed down during actual implementation to Voyage-only: one `VoyageEmbedder` class, one `chunks` table with fixed `vector(1024)`, `Config` has no `embedding_provider` field. Confirmed with user 2026-08-18 to keep this narrower scope rather than backfilling NIM support now — revisit as its own task if/when NIM is actually needed. (Task 11 later added `nim_api_key`/`generation_provider` to `Config` for generation fallback only — embedding stayed Voyage-only.)
- **Task 10 (vector search)**: done. `sleuth/retrieve/search.py` — `search_chunks(conn, repo_id, query_embedding, top_k=8)`, pgvector `<=>` cosine distance, HNSW index does the work. No `dim` param (single-table scope, see above). `tests/test_search.py`.
- **Task 11 (pluggable Generator + answer generation)**: done. `sleuth/llm/generate.py` — `Generator` ABC, `GroqGenerator`/`NimGenerator`, `get_fallback_chain`/`chat_with_fallback` (Groq-primary, NIM-fallback only if `nim_api_key` set). `sleuth/retrieve/answer.py` — `stream_answer`/`get_answer` glue retrieval+generation. `tests/test_generate.py`, `tests/test_answer.py`.
- **Task 12 (CLI)**: done. `sleuth/cli.py`, `sleuth/__main__.py` — `add`/`list`/`ask`/`agentic`/`eval` subcommands. `agentic` skips DB entirely (live mode never touches Postgres). `tests/test_cli.py`. Caught and fixed a real bug here: `apply_schema()` re-runs `schema.sql` (including an `ALTER TABLE`, which needs an ACCESS EXCLUSIVE lock) on every single CLI invocation — a test fixture holding an uncommitted read on `repos` caused a later command to hang forever waiting on that lock, no `lock_timeout` configured anywhere. Fixed the test (commit after the read), then closed the underlying fragility for real: `get_connection()` now sets `lock_timeout = '5s'` on every connection, so any statement blocked on a lock fails fast with `psycopg.errors.LockNotAvailable` instead of hanging silently — `tests/test_db.py::test_connection_lock_timeout_fails_fast_instead_of_hanging`. Deliberately kept `apply_schema()` re-applying every call rather than caching "already applied" — there's no migration framework, so unconditional idempotent re-apply is how a future `schema.sql` edit ever reaches an already-provisioned DB; caching would silently break that.
- **Task 13 (agentic/live retrieval mode)**: done. `sleuth/retrieve/agentic.py` — `run_agentic(question, path, config, generator=None)`, text-protocol tool loop (`TOOL: <name> {json}` or plain-prose final answer) over three hand-written tools (`grep`, `list_files`, `read_file`), capped at 6 iterations/50 grep matches/400 read_file lines. Tool-selection turns are non-streaming (`stream=False`) — the loop needs the full response to tell tool-call vs. final answer. `tests/test_agentic.py`.
- **Task 14 (eval harness)**: done. `sleuth/eval/runner.py` — `load_golden(path)`, `run_eval(golden_yaml_path, conn, config)` scores each golden case on hit (file/symbol match), reciprocal rank, and an LLM-judge 1-5 score, aggregates to hit-rate@8/MRR/avg judge. Voyage-only, same narrowed scope as Tasks 7-11 (no `_EMBEDDER_BY_MODEL`, no `dim` param). `eval/sample_repo.yaml` (template for real repos), `tests/fixtures/sample_golden.yaml` (test fixture), `tests/test_eval_runner.py`. Added `pyyaml` to `requirements.txt`.
- **All 14 tasks done.** Full suite green: 64/64 passing as of last check.

## Progress — Plan 2 (FastAPI + React web app)

Plan doc: `docs/superpowers/plans/2026-08-19-rag-web-app-fastapi-react.md` (13 tasks, Task 0-12).

- **Task 0 (auth)**: done, but **diverged from the plan doc**. Plan doc specifies GitHub OAuth + email magic-link. Actually built: email/password auth instead — `sleuth/api/routes/auth.py` (`POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`) hashes/checks passwords with `bcrypt`, `sleuth/api/auth/session.py` signs a `sleuth_session` cookie via `itsdangerous.URLSafeTimedSerializer(config.session_secret)`, `require_session` FastAPI dependency reads it and loads the user via `store.get_user`. `sleuth/api/routes/users.py` — `GET /me`, `PATCH /me` (theme). `users` table added to `schema.sql` with `password_hash text` (nullable, `ALTER TABLE ADD COLUMN IF NOT EXISTS` for the pre-existing OAuth-shaped table). `Config` only gained `session_secret`; the `github_client_id`/`github_client_secret`/`smtp_*` fields the plan doc calls for were added then **removed** — GitHub OAuth (`sleuth/api/auth/github.py`) and email magic-link (`sleuth/api/auth/email_link.py`) were built first, then deleted in favor of the simpler password flow (commit "feat: Updated plan and simplified auth"). `docs/progress.html` has both the original and revised writeups. `tests/test_api_auth.py` covers the password flow. **The plan doc's Task 0 section (line ~262) is stale** — describes the abandoned OAuth/magic-link design; Tasks 1+ in that doc still apply.
- **Task 1 (store helper + repo endpoints, behind auth)**: done. `sleuth/api/routes/repos.py` — `POST /repos` (creates a `pending` repo row, kicks off `ingest_repo` via FastAPI `BackgroundTasks`), `GET /repos`, `GET /repos/{id}` (404 if missing) — router-level `Depends(require_session)` gates all three. `sleuth/api/main.py` — `create_app(config)` wires CORS (`allow_credentials=True`, origin `http://localhost:5173`) and the same `get_connection`/`apply_schema`-per-request middleware pattern as the CLI. `tests/test_api_repos.py` (stubs `ingest_repo` so the round-trip test doesn't shell out to a real `git clone`).
- **Tasks 2-12** (progress instrumentation, chat persistence + SSE streaming, Vite scaffold, login/landing/repos/indexing/chat screens, theme switcher, polish): not started yet.
- Not yet run in this session: could not execute `pytest tests/test_api_auth.py tests/test_api_repos.py` end-to-end here — native-Windows Python (`.venv-win`) couldn't reach the Docker Postgres container on `localhost:5433` (connection timeout from PowerShell/`.venv-win`, but reachable from a Windows-side `python3` stub run through the Bash tool) — looks like a host-networking quirk specific to this check, not a code defect; verified Task 0/1 by reading code + `docs/progress.html` + `schema.sql` instead. Worth a clean re-run before Task 2.
- Also present, not part of Plan 2, not started: `docs/superpowers/specs/2026-08-24-call-graph-extraction-design.md` + matching plan `docs/superpowers/plans/2026-08-24-call-graph-extraction.md` — a separate future feature, added same day as the auth simplification, no code written against it yet.

## Environment notes

- Repo lives at `/mnt/d/Personal/SLEUTH` in WSL (`D:\Personal\SLEUTH` on Windows / `/d/Personal/SLEUTH` in Git Bash).
- Docker is run from **Git Bash on Windows**, not from inside WSL (no WSL Docker integration enabled). Any `docker compose` command should be given to the user to run in Git Bash, not executed directly in this WSL session.
- WSL2 forwards Windows `localhost` ports automatically, so containers started from Windows-side Docker are reachable at `localhost:<port>` from WSL — except where a native WSL-local service is already bound to that same port (see Task 2 note above).
- Python venv at `.venv/` (created via `python3 -m venv .venv`), deps installed from `requirements.txt`. This is WSL-native — its `bin/python` symlinks are broken when accessed from Windows (Git Bash/PowerShell); use it only from actual WSL.
- A second venv, `.venv-win/`, is a native-Windows Python 3.11 install for running things from PowerShell/Git-Bash-on-Windows directly. Kept separately from `requirements.txt` sync — `tree-sitter-vue`'s wheel needs a C++ build toolchain that isn't installed natively on Windows (`pip install -r requirements.txt` fails on it), so install packages there one at a time as needed instead of the full requirements file.
- Global git config on this machine has an unrelated bug (`git config --global --list` fails with `fatal: cannot chdir to 'D:/'`) even with no `~/.gitconfig` present — worked around by using local (per-repo) git config only. Not investigated further, not blocking.
- `docs/progress.html` — self-contained build log, one section per completed task, meant to be opened directly in a browser.
- `Note.md` (repo root) — user's own understanding notes, written by them after each task, reviewed/corrected by Claude on request.
