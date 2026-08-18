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
Implementation plan (12 tasks, TDD, real code per step): `docs/superpowers/plans/2026-08-06-rag-core-pipeline-cli.md`
— **plan predates v2 design**; still valid for Tasks 1-2 done so far, but tasks
covering retrieval/providers/eval will need a check against v2 before building
(pluggable providers, agentic mode, eval harness weren't in the plan's source spec).

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

Work through the plan's 12 tasks **one full task at a time**, not step-by-step
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

- **Task 1 (config)**: done. `requirements.txt`, `.env.example`, `.gitignore`, `sleuth/config.py`, `tests/test_config.py` all written, tests passing (3/3). Not yet committed by user as of last check.
- **Task 2 (Postgres schema + connection)**: done. `schema.sql`, `docker-compose.yml`, `sleuth/db.py`, `tests/conftest.py`, `tests/test_db.py` all written, tests passing (4/4 incl. Task 1's). Container runs on host port **5433** (native WSL Postgres 18 occupies 5432 — see below, do **not** touch it). Hit one extra snag on top of the port issue: `get_connection()` registers the pgvector `vector` type on connect, but a fresh DB has no `vector` extension until `apply_schema()` runs — chicken-and-egg. Fixed by bootstrapping `CREATE EXTENSION IF NOT EXISTS vector`/`pgcrypto` once by hand against the fresh container; extensions persist, so this is a one-time step per fresh volume, not per connection. Not yet committed by user as of last check.
- **Tasks 3-12**: not started.

## Environment notes

- Repo lives at `/mnt/d/Personal/SLEUTH` in WSL (`D:\Personal\SLEUTH` on Windows / `/d/Personal/SLEUTH` in Git Bash).
- Docker is run from **Git Bash on Windows**, not from inside WSL (no WSL Docker integration enabled). Any `docker compose` command should be given to the user to run in Git Bash, not executed directly in this WSL session.
- WSL2 forwards Windows `localhost` ports automatically, so containers started from Windows-side Docker are reachable at `localhost:<port>` from WSL — except where a native WSL-local service is already bound to that same port (see Task 2 note above).
- Python venv at `.venv/` (created via `python3 -m venv .venv`), deps installed from `requirements.txt`.
- Global git config on this machine has an unrelated bug (`git config --global --list` fails with `fatal: cannot chdir to 'D:/'`) even with no `~/.gitconfig` present — worked around by using local (per-repo) git config only. Not investigated further, not blocking.
- `docs/progress.html` — self-contained build log, one section per completed task, meant to be opened directly in a browser.
- `Note.md` (repo root) — user's own understanding notes, written by them after each task, reviewed/corrected by Claude on request.
