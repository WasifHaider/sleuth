# Sleuth

RAG chatbot over GitHub repos — point it at a repo, ask questions about the
code, get answers grounded in the actual source. See `CLAUDE.md` for the full
project context, design docs, and build history.

## What Sleuth does

- **Structure-aware indexing** — tree-sitter parses each file to an AST and
  chunks at function/method/class boundaries (not fixed-size text windows),
  so a retrieved chunk is always a complete unit of code with its signature
  and enclosing scope intact.
- **Two retrieval modes** — *indexed* (clone → parse → chunk → embed →
  pgvector, for a connected GitHub repo) and *agentic/live* (a hand-written
  tool loop — `grep`/`list_files`/`read_file` — over the current local
  directory, no indexing wait, no Postgres involved).
- **Code ranked ahead of documentation** — chunks under any `docs`/`doc`
  directory are tagged and labeled `[DOCUMENTATION]` in the prompt, and
  retrieval always exhausts real `[CODE]` matches before falling back to
  docs, so architecture write-ups can't crowd out the actual implementation
  they describe.
- **Global/architecture question routing** — broad questions ("summarize the
  whole project", "rate this architecture") are detected by a keyword
  classifier and answered against a repo-wide summary generated at index
  time, instead of forcing a whole-repo question through top-k chunk search.
- **Web app** — connect/re-index/delete repos, multiple chats per repo,
  streamed (SSE) cited answers, live indexing progress, theme switching.
- **`sleuth eval`** — a golden-set YAML per repo scores retrieval (hit-rate@k,
  MRR) and answer quality (LLM-judge 1–5) end to end, so retrieval/prompt
  changes are checked against a regression suite instead of eyeballed.
- **Standalone REPL (`sleuth-repl`)** — the agentic tool-loop mode packaged
  on its own with zero Postgres/Voyage/tree-sitter dependencies, for asking
  questions against any local codebase with just a Groq key.

No vendor RAG SDKs (Voyage/NIM/Groq are raw REST calls via `httpx`), no ORM
(raw SQL via `psycopg`) — every piece of the pipeline is hand-written, see
`CLAUDE.md` for why.

## Running locally

Requires Postgres with the `vector` extension — either the local Docker
container (`docker compose up -d`, from Git Bash on Windows if you're on
WSL2 — see `CLAUDE.md`'s Environment notes) or a Supabase project.

Backend (from repo root):

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill in VOYAGE_API_KEY, GROQ_API_KEY, DATABASE_URL
    uvicorn sleuth.api.main:app --reload

Frontend:

    cd web
    cp .env.example .env
    npm install
    npm run dev

Open http://localhost:5717 (the frontend dev server's port — see
`web/vite.config.js`). The API runs on http://localhost:8000.

## Tests

    pytest                 # backend — needs TEST_DATABASE_URL in .env
    cd web && npm run build  # frontend — no test suite yet, build is the smoke check

## CLI

The core pipeline (clone → chunk → embed → store → retrieve → generate) is
also available directly, without the web app:

    python -m sleuth add <github_url>
    python -m sleuth list
    python -m sleuth ask <repo_id> "<question>"
    python -m sleuth agentic <path> "<question>"
    python -m sleuth eval <golden_yaml_path>

`agentic` runs live retrieval (grep/list_files/read_file over the current
local directory) and never touches Postgres — no indexing wait, works on any
local checkout. `eval` scores retrieval + answer quality against a golden-set
YAML — see `eval/sample_repo.yaml` for the format, and `eval/sleuth_golden_easy.yaml`
/ `eval/sleuth_golden_hard.yaml` for real golden sets run against this repo
itself.

<!-- eval numbers: TBD -->

## Standalone REPL package (`sleuth-repl`)

The interactive agentic REPL (bare `sleuth` with no subcommand) is also
distributed on its own, with none of the Postgres/Voyage/tree-sitter
machinery the rest of this repo needs — it only ever reads your local
codebase (grep/list_files/read_file) and calls Groq for generation.

    pip install .          # from repo root, builds the sleuth-repl package
    sleuth [path]           # launches the REPL against `path` (default: cwd)

First run prompts for a Groq API key (free at
https://console.groq.com/keys) and saves it to `~/.sleuth/config.json` —
every later run on that machine reads it back, no re-entry needed. Set the
`GROQ_API_KEY` env var instead to override the saved key for one run, or
`SLEUTH_CONFIG_PATH` to change where the key is stored.

This package intentionally does NOT include `add`/`list`/`ask`/`eval` —
those stay part of the full monorepo checkout above, since they need a real
Postgres + Voyage/Groq server-side setup this package deliberately ships
without.
