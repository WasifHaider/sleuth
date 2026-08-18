# RAG Core Pipeline + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core ingest→embed→store→retrieve→answer pipeline for the RAG code chatbot, exposed through a CLI, so a user can point it at a GitHub repo and ask questions about it, run it live/agentic in a local directory, and evaluate retrieval quality — no FastAPI/React yet (that's Plan 2).

**Design doc:** `docs/superpowers/specs/2026-08-13-rag-code-chatbot-design-v2.md` (current). Supersedes `docs/superpowers/specs/2026-08-06-rag-code-chatbot-design.md` (v1) — this plan revision aligns Plan 1's scope with v2: pluggable embedding/generation providers, per-repo embedding model tracking, agentic/live retrieval mode, and the `sleuth eval` harness are now part of Plan 1 (they're CLI/pipeline-level, not web-level, so they don't belong to Plan 2).

**Architecture:** A Python package `sleuth/` with three pipelines sharing a Postgres (pgvector) store: `ingest` (clone → tree-sitter parse → chunk → hash-diff → embed → upsert), `retrieve` (indexed: embed query → vector search → prompt → generate; agentic: tool loop over the local filesystem), and `eval` (golden-set runner: retrieval hit-rate/MRR + LLM-judge answer quality). Every module is plain functions/classes with no framework dependency, so the CLI (this plan) and the future FastAPI layer (Plan 2) call the exact same code.

**Tech Stack:** Python 3.11+, tree-sitter (+ tree-sitter-python/javascript/typescript grammars), httpx (direct REST calls to Voyage/NIM/Groq — no vendor SDKs, so every request/response is visible), psycopg 3 + pgvector (direct SQL against Postgres, no ORM), pytest/pytest-asyncio/respx for tests, PyYAML (golden-set files), Docker (local Postgres+pgvector for dev/tests, schema is identical to Supabase since Supabase is just Postgres).

## Global Constraints

- Language: Python for all backend/pipeline code (per spec).
- Chunking granularity: function/method/top-level-class via tree-sitter AST walk, plus one module-level fallback chunk per file for top-level code outside any function/class (per spec, "Chunking Rationale").
- Initial language support: Python, JavaScript, TypeScript only (per spec Non-Goals — other languages are additive later).
- Embeddings are **pluggable**: `EMBEDDING_PROVIDER` = `voyage` (default, `voyage-code-3`, dim 1024) or `nim` (`nemotron-3-embed-1b`, dim 2048, no output-dimension truncation). **One embedding model per repo, hard rule** — recorded on `repos.embedding_model`/`repos.embedding_dim` at first successful index; question embeddings always use the repo's recorded model, not the current config default. Because pgvector columns are fixed-width, chunks live in per-dimension tables (`chunks_1024`, `chunks_2048`), selected by the repo's `embedding_dim`.
- Generation is **pluggable**: `GENERATION_PROVIDER` = `groq` (default, model configurable via `GROQ_MODEL`) or `nim`. Groq → NIM is also the automatic fallback chain on persistent 429/5xx (one retry-with-backoff on the primary first, then failover), regardless of which is primary.
- Vector store: Postgres + pgvector, schema exactly as defined in the v2 Data Model section (`repos`, `chunks_1024`, `chunks_2048` tables), reachable via a plain connection string (Supabase in prod, local Docker Postgres in dev/tests — same schema, no code branches for environment).
- Re-indexing must be incremental: skip re-embedding chunks whose `content_hash` is unchanged since the last index — **unless** the repo's recorded `embedding_model` differs from the currently configured provider's model, in which case every chunk is treated as needing re-embedding and the recorded model/dim is updated.
- Embedding calls must be sent with bounded concurrency (async), not strictly sequential.
- Generated answers stream token-by-token to the terminal (both indexed-mode `ask` and agentic mode) — time-to-first-token is the felt-speed metric.
- Agentic/live mode is a fixed-budget tool loop: max 6 tool iterations per question; `grep` caps at 50 matches; `read_file` caps at 400 lines per call; loop ends on a non-tool-call model response, or on hitting the iteration cap (in which case it answers with what it has and says the search was cut short).
- `sleuth eval` is a first-class regression suite, not optional tooling — any change to chunking/retrieval is expected to be checked against it.
- Error handling: a single file that fails to parse must not abort the whole repo index (skip + continue); a repo that fails to clone must land in `repos.status = 'failed'` with `error_message` set, not raise past the pipeline boundary uncaught; a chat query against a repo whose status isn't `'ready'` must be rejected with a clear error before any LLM call; persistent failure of the whole generation fallback chain surfaces as a clear error, does not crash the process.
- No vendor SDKs for Voyage/NIM/Groq — call their HTTP APIs directly via httpx so every request is inspectable code, not library internals.

---

## File Structure

```
sleuth/
  __init__.py
  __main__.py            # `python -m sleuth` entry point
  config.py               # env-var driven, provider-aware Config
  db.py                    # connection + schema application
  store.py                 # repo/chunk CRUD (raw SQL), per-dimension table selection
  chunking.py              # Chunk dataclass + context formatting (shared by embed + answer)
  http_retry.py             # shared retry-with-backoff wrapper for all provider calls
  cli.py                    # argparse CLI: add / list / ask / agentic / eval
  ingest/
    __init__.py
    clone.py                # shallow git clone + file listing
    parse.py                 # tree-sitter language registry + parse_source()
    chunk.py                 # chunk_source(): walks AST -> list[Chunk]
    embed.py                  # Embedder ABC + VoyageEmbedder/NimEmbedder + get_embedder() factory
    pipeline.py               # orchestrates ingest_repo()
  llm/
    __init__.py
    generate.py                # Generator ABC + GroqGenerator/NimGenerator + fallback chain + get_generator()
  retrieve/
    __init__.py
    search.py                 # pgvector similarity search, per-dimension table selection
    answer.py                  # prompt building + streaming answer (indexed mode)
    agentic.py                 # live/agentic tool loop (grep/list_files/read_file)
  eval/
    __init__.py
    runner.py                  # sleuth eval: golden YAML -> hit-rate/MRR/judge score table
schema.sql                    # repos + chunks_1024 + chunks_2048 tables, pgvector extension, indexes
docker-compose.yml             # local Postgres+pgvector for dev/tests
requirements.txt
.env.example
eval/
  sample_repo.yaml             # example golden set for manual eval runs
tests/
  conftest.py
  test_config.py
  test_chunking.py
  test_parse.py
  test_chunk.py
  test_clone.py
  test_db.py
  test_store.py
  test_http_retry.py
  test_embed.py
  test_search.py
  test_answer.py
  test_pipeline.py
  test_generate.py
  test_agentic.py
  test_eval_runner.py
  test_cli.py
  fixtures/
    sample_golden.yaml
```

Design decision worth calling out: a class is only ever chunked as a whole (`kind='class'`) if it has **no** method-like children. If it has methods, each method becomes its own chunk (`symbol_name = "ClassName.method_name"`) and the class itself is not separately chunked — avoids storing the same code twice (once inside the class chunk, once inside each method chunk).

---

### Task 1: Project scaffolding and provider-aware config

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `sleuth/__init__.py`
- Create: `sleuth/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `sleuth.config.Config` dataclass with fields `embedding_provider: str`, `generation_provider: str`, `voyage_api_key: str | None`, `nim_api_key: str | None`, `groq_api_key: str | None`, `groq_model: str`, `database_url: str`; `sleuth.config.load_config() -> Config`; `sleuth.config.ConfigError(Exception)`.

- [ ] **Step 1: Write requirements.txt**

```
tree-sitter>=0.23,<0.24
tree-sitter-python>=0.23,<0.24
tree-sitter-javascript>=0.23,<0.24
tree-sitter-typescript>=0.23,<0.24
httpx>=0.27,<0.28
psycopg[binary]>=3.1,<4
pgvector>=0.3,<0.4
python-dotenv>=1.0,<2
pyyaml>=6.0,<7
pytest>=8,<9
pytest-asyncio>=0.24,<0.25
respx>=0.21,<0.22
```

- [ ] **Step 2: Write .env.example**

```
EMBEDDING_PROVIDER=voyage
GENERATION_PROVIDER=groq
VOYAGE_API_KEY=
NIM_API_KEY=
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/sleuth
```

- [ ] **Step 3: Create sleuth/__init__.py (empty)**

```python
```

- [ ] **Step 4: Write the failing test**

```python
# tests/test_config.py
import pytest
from sleuth.config import load_config, ConfigError


def test_load_config_reads_all_values(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.embedding_provider == "voyage"
    assert config.generation_provider == "groq"
    assert config.voyage_api_key == "voyage-key"
    assert config.groq_api_key == "groq-key"
    assert config.groq_model == "llama-3.3-70b-versatile"
    assert config.database_url == "postgresql://u:p@localhost:5432/db"


def test_load_config_defaults_groq_model_and_providers(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("GENERATION_PROVIDER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.groq_model == "llama-3.3-70b-versatile"
    assert config.embedding_provider == "voyage"
    assert config.generation_provider == "groq"


def test_load_config_raises_on_missing_required_var(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        load_config()


def test_load_config_nim_embedding_provider_requires_nim_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "nim")
    monkeypatch.delenv("NIM_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    with pytest.raises(ConfigError, match="NIM_API_KEY"):
        load_config()


def test_load_config_nim_embedding_provider_succeeds_with_nim_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "nim")
    monkeypatch.setenv("NIM_API_KEY", "nim-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.embedding_provider == "nim"
    assert config.nim_api_key == "nim-key"
    assert config.voyage_api_key is None
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.config'`

- [ ] **Step 6: Write sleuth/config.py**

```python
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_EMBEDDING_PROVIDER = "voyage"
DEFAULT_GENERATION_PROVIDER = "groq"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    embedding_provider: str
    generation_provider: str
    voyage_api_key: str | None
    nim_api_key: str | None
    groq_api_key: str | None
    groq_model: str
    database_url: str


def load_config() -> Config:
    embedding_provider = os.environ.get("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER)
    generation_provider = os.environ.get("GENERATION_PROVIDER", DEFAULT_GENERATION_PROVIDER)

    missing = []
    if not os.environ.get("DATABASE_URL"):
        missing.append("DATABASE_URL")

    if embedding_provider == "voyage" and not os.environ.get("VOYAGE_API_KEY"):
        missing.append("VOYAGE_API_KEY")
    elif embedding_provider == "nim" and not os.environ.get("NIM_API_KEY"):
        missing.append("NIM_API_KEY")

    if generation_provider == "groq" and not os.environ.get("GROQ_API_KEY"):
        missing.append("GROQ_API_KEY")
    elif generation_provider == "nim" and not os.environ.get("NIM_API_KEY"):
        missing.append("NIM_API_KEY")

    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    return Config(
        embedding_provider=embedding_provider,
        generation_provider=generation_provider,
        voyage_api_key=os.environ.get("VOYAGE_API_KEY"),
        nim_api_key=os.environ.get("NIM_API_KEY"),
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        database_url=os.environ["DATABASE_URL"],
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example sleuth/__init__.py sleuth/config.py tests/test_config.py
git commit -m "feat: add project scaffolding and provider-aware config loading"
```

---

### Task 2: Postgres schema, local dev DB, and connection helper

**Files:**
- Create: `schema.sql`
- Create: `docker-compose.yml`
- Create: `sleuth/db.py`
- Create: `tests/conftest.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `sleuth.db.get_connection(database_url: str) -> psycopg.Connection` (registers pgvector adapter), `sleuth.db.apply_schema(conn) -> None`.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write schema.sql**

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS repos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_url      text NOT NULL,
    status          text NOT NULL DEFAULT 'pending',
    error_message   text,
    embedding_model text,
    embedding_dim   int,
    indexed_at      timestamptz
);

-- Chunks live in per-dimension tables since pgvector columns are fixed-width
-- and a repo's embedding model (and therefore dim) can only change via a
-- full re-index. sleuth/store.py and sleuth/retrieve/search.py (Tasks 8/10)
-- select chunks_1024 vs chunks_2048 by the repo's recorded embedding_dim.

CREATE TABLE IF NOT EXISTS chunks_1024 (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id       uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_path     text NOT NULL,
    symbol_name   text,
    kind          text NOT NULL,
    start_line    int NOT NULL,
    end_line      int NOT NULL,
    code_text     text NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(1024) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_1024_identity_idx
    ON chunks_1024 (repo_id, file_path, COALESCE(symbol_name, ''));

CREATE INDEX IF NOT EXISTS chunks_1024_embedding_idx
    ON chunks_1024 USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS chunks_2048 (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id       uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_path     text NOT NULL,
    symbol_name   text,
    kind          text NOT NULL,
    start_line    int NOT NULL,
    end_line      int NOT NULL,
    code_text     text NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(2048) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_2048_identity_idx
    ON chunks_2048 (repo_id, file_path, COALESCE(symbol_name, ''));

CREATE INDEX IF NOT EXISTS chunks_2048_embedding_idx
    ON chunks_2048 USING hnsw (embedding vector_cosine_ops);
```

Note: `gen_random_uuid()` needs `pgcrypto` (or Postgres 13+'s built-in `gen_random_uuid()` — Supabase and the `pgvector/pgvector` Docker image both ship Postgres 15+, so this is present either way; the `CREATE EXTENSION pgcrypto` line is a harmless no-op safety net).

- [ ] **Step 2: Write docker-compose.yml**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: sleuth
    ports:
      - "5432:5432"
    volumes:
      - sleuth_pg_data:/var/lib/postgresql/data

volumes:
  sleuth_pg_data:
```

Start it with `docker compose up -d` before running any DB-dependent test in this plan.

- [ ] **Step 3: Write tests/conftest.py**

```python
import os

import pytest

from sleuth.db import apply_schema, get_connection

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sleuth"
)


@pytest.fixture
def pg_conn():
    conn = get_connection(TEST_DATABASE_URL)
    apply_schema(conn)
    conn.execute("TRUNCATE repos CASCADE")
    conn.commit()
    yield conn
    conn.close()
```

- [ ] **Step 4: Write the failing test**

```python
# tests/test_db.py
from sleuth.db import apply_schema, get_connection
from tests.conftest import TEST_DATABASE_URL


def test_apply_schema_creates_tables():
    conn = get_connection(TEST_DATABASE_URL)
    apply_schema(conn)

    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    table_names = {row[0] for row in rows}

    assert "repos" in table_names
    assert "chunks_1024" in table_names
    assert "chunks_2048" in table_names
    conn.close()


def test_repos_table_has_embedding_model_and_dim_columns():
    conn = get_connection(TEST_DATABASE_URL)
    apply_schema(conn)

    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'repos'"
    ).fetchall()
    column_names = {row[0] for row in rows}

    assert "embedding_model" in column_names
    assert "embedding_dim" in column_names
    conn.close()
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.db'`

- [ ] **Step 6: Write sleuth/db.py**

```python
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_connection(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, autocommit=False)
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.execute(sql)
    conn.commit()
```

- [ ] **Step 7: Run test to verify it passes**

Run: `docker compose up -d && sleep 2 && pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add schema.sql docker-compose.yml sleuth/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: add Postgres schema (per-dimension chunks tables) and connection helper"
```

---

### Task 3: Chunk data model and context formatting

**Files:**
- Create: `sleuth/chunking.py`
- Test: `tests/test_chunking.py`

**Interfaces:**
- Produces: `sleuth.chunking.Chunk` dataclass (`file_path: str`, `symbol_name: str | None`, `kind: str`, `start_line: int`, `end_line: int`, `code_text: str`, property `content_hash: str`); `sleuth.chunking.format_chunk_context(chunk: Chunk, language: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunking.py
from sleuth.chunking import Chunk, format_chunk_context


def test_content_hash_deterministic_and_sensitive_to_text():
    a = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    b = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    c = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 2\n")

    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash


def test_format_chunk_context_includes_metadata_and_code():
    chunk = Chunk("pkg/mod.py", "Bar.method_a", "method", 5, 7, "def method_a(self):\n    return 2\n")

    text = format_chunk_context(chunk, "python")

    assert "pkg/mod.py" in text
    assert "Bar.method_a" in text
    assert "method" in text
    assert "python" in text
    assert "def method_a(self):" in text


def test_format_chunk_context_handles_module_level_symbol_none():
    chunk = Chunk("pkg/mod.py", None, "module", 1, 2, "import os\n")

    text = format_chunk_context(chunk, "python")

    assert "module" in text
    assert "import os" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chunking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.chunking'`

- [ ] **Step 3: Write sleuth/chunking.py**

```python
import hashlib
from dataclasses import dataclass


@dataclass
class Chunk:
    file_path: str
    symbol_name: str | None
    kind: str  # 'function' | 'method' | 'class' | 'module'
    start_line: int
    end_line: int
    code_text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.code_text.encode("utf-8")).hexdigest()


def format_chunk_context(chunk: Chunk, language: str) -> str:
    symbol = chunk.symbol_name or "(module level)"
    header = (
        f"# File: {chunk.file_path}\n"
        f"# {chunk.kind}: {symbol}\n"
        f"# Language: {language}\n\n"
    )
    return header + chunk.code_text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chunking.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/chunking.py tests/test_chunking.py
git commit -m "feat: add Chunk model and context formatting"
```

---

### Task 4: tree-sitter language registry and parsing

**Files:**
- Create: `sleuth/ingest/__init__.py`
- Create: `sleuth/ingest/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Produces: `sleuth.ingest.parse.LanguageSpec` dataclass (`key: str`, `ts_language`), `sleuth.ingest.parse.LANGUAGES: dict[str, LanguageSpec]` (keyed by extension, e.g. `.py`), `sleuth.ingest.parse.parse_source(source_bytes: bytes, extension: str) -> tuple[Tree, LanguageSpec]`, `sleuth.ingest.parse.UnsupportedFileType(Exception)`.

- [ ] **Step 1: Create sleuth/ingest/__init__.py (empty)**

```python
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_parse.py
import pytest

from sleuth.ingest.parse import UnsupportedFileType, parse_source


def test_parse_source_python_no_errors():
    source = b"def foo():\n    return 1\n"
    tree, spec = parse_source(source, ".py")

    assert spec.key == "python"
    assert tree.root_node.type == "module"
    assert tree.root_node.has_error is False


def test_parse_source_javascript_no_errors():
    source = b"function foo() { return 1; }\n"
    tree, spec = parse_source(source, ".js")

    assert spec.key == "javascript"
    assert tree.root_node.has_error is False


def test_parse_source_unsupported_extension_raises():
    with pytest.raises(UnsupportedFileType):
        parse_source(b"irrelevant", ".rb")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.parse'`

- [ ] **Step 4: Write sleuth/ingest/parse.py**

```python
from dataclasses import dataclass

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser, Tree

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())


@dataclass(frozen=True)
class LanguageSpec:
    key: str
    ts_language: Language


LANGUAGES: dict[str, LanguageSpec] = {
    ".py": LanguageSpec("python", PY_LANGUAGE),
    ".js": LanguageSpec("javascript", JS_LANGUAGE),
    ".ts": LanguageSpec("typescript", TS_LANGUAGE),
}


class UnsupportedFileType(Exception):
    pass


def parse_source(source_bytes: bytes, extension: str) -> tuple[Tree, LanguageSpec]:
    spec = LANGUAGES.get(extension)
    if spec is None:
        raise UnsupportedFileType(extension)

    parser = Parser(spec.ts_language)
    tree = parser.parse(source_bytes)
    return tree, spec
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_parse.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add sleuth/ingest/__init__.py sleuth/ingest/parse.py tests/test_parse.py
git commit -m "feat: add tree-sitter language registry and parsing"
```

---

### Task 5: Chunker — walk the AST into Chunks

**Files:**
- Create: `sleuth/ingest/chunk.py`
- Test: `tests/test_chunk.py`

**Interfaces:**
- Consumes: `sleuth.ingest.parse.parse_source`, `sleuth.ingest.parse.UnsupportedFileType`, `sleuth.chunking.Chunk`.
- Produces: `sleuth.ingest.chunk.chunk_source(source_bytes: bytes, file_path: str, extension: str) -> list[Chunk]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunk.py
from sleuth.ingest.chunk import chunk_source

PYTHON_SOURCE = b'''import os

MAX = 10

def foo():
    return 1

class Bar:
    def method_a(self):
        return 2

    def method_b(self):
        return 3

class Empty:
    pass
'''


def test_chunk_source_python_produces_expected_chunks():
    chunks = chunk_source(PYTHON_SOURCE, "pkg/mod.py", ".py")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert set(by_symbol) == {None, "foo", "Bar.method_a", "Bar.method_b", "Empty"}
    assert by_symbol[None].kind == "module"
    assert "import os" in by_symbol[None].code_text
    assert "MAX = 10" in by_symbol[None].code_text

    assert by_symbol["foo"].kind == "function"
    assert by_symbol["foo"].start_line == 5
    assert by_symbol["foo"].end_line == 6

    assert by_symbol["Bar.method_a"].kind == "method"
    assert by_symbol["Bar.method_b"].kind == "method"
    assert by_symbol["Empty"].kind == "class"

    # class with methods is not also emitted as its own whole-class chunk
    assert "Bar" not in by_symbol


JS_SOURCE = b'''const path = require("path");

function foo() {
  return 1;
}

class Bar {
  methodA() {
    return 2;
  }
}
'''


def test_chunk_source_javascript_produces_expected_chunks():
    chunks = chunk_source(JS_SOURCE, "src/mod.js", ".js")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert set(by_symbol) == {None, "foo", "Bar.methodA"}
    assert by_symbol["foo"].kind == "function"
    assert by_symbol["Bar.methodA"].kind == "method"


def test_chunk_source_unsupported_extension_returns_empty_list():
    assert chunk_source(b"puts 1", "script.rb", ".rb") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chunk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.chunk'`

- [ ] **Step 3: Write sleuth/ingest/chunk.py**

```python
from sleuth.chunking import Chunk
from sleuth.ingest.parse import UnsupportedFileType, parse_source


def chunk_source(source_bytes: bytes, file_path: str, extension: str) -> list[Chunk]:
    try:
        tree, spec = parse_source(source_bytes, extension)
    except UnsupportedFileType:
        return []

    if spec.key == "python":
        return _walk_python(tree.root_node, source_bytes, file_path)
    return _walk_js_like(tree.root_node, source_bytes, file_path)


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def _node_name(node, source_bytes: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    return _node_text(name_node, source_bytes) if name_node is not None else None


def _walk_python(root, source_bytes: bytes, file_path: str) -> list[Chunk]:
    return _walk_generic(
        root,
        source_bytes,
        file_path,
        function_type="function_definition",
        class_type="class_definition",
        method_type="function_definition",
    )


def _walk_js_like(root, source_bytes: bytes, file_path: str) -> list[Chunk]:
    return _walk_generic(
        root,
        source_bytes,
        file_path,
        function_type="function_declaration",
        class_type="class_declaration",
        method_type="method_definition",
    )


def _walk_generic(
    root, source_bytes: bytes, file_path: str, *, function_type: str, class_type: str, method_type: str
) -> list[Chunk]:
    chunks: list[Chunk] = []
    leftover_nodes = []

    for child in root.children:
        if child.type == function_type:
            name = _node_name(child, source_bytes)
            chunks.append(
                Chunk(
                    file_path=file_path,
                    symbol_name=name,
                    kind="function",
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    code_text=_node_text(child, source_bytes),
                )
            )
        elif child.type == class_type:
            class_name = _node_name(child, source_bytes)
            body = child.child_by_field_name("body")
            method_nodes = [c for c in (body.children if body else []) if c.type == method_type]

            if method_nodes:
                for method in method_nodes:
                    method_name = _node_name(method, source_bytes)
                    chunks.append(
                        Chunk(
                            file_path=file_path,
                            symbol_name=f"{class_name}.{method_name}",
                            kind="method",
                            start_line=method.start_point[0] + 1,
                            end_line=method.end_point[0] + 1,
                            code_text=_node_text(method, source_bytes),
                        )
                    )
            else:
                chunks.append(
                    Chunk(
                        file_path=file_path,
                        symbol_name=class_name,
                        kind="class",
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        code_text=_node_text(child, source_bytes),
                    )
                )
        else:
            leftover_nodes.append(child)

    if leftover_nodes:
        chunks.append(
            Chunk(
                file_path=file_path,
                symbol_name=None,
                kind="module",
                start_line=leftover_nodes[0].start_point[0] + 1,
                end_line=leftover_nodes[-1].end_point[0] + 1,
                code_text="\n".join(_node_text(n, source_bytes) for n in leftover_nodes),
            )
        )

    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chunk.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/ingest/chunk.py tests/test_chunk.py
git commit -m "feat: add tree-sitter AST chunker"
```

---

### Task 6: Git clone and file listing

**Files:**
- Create: `sleuth/ingest/clone.py`
- Test: `tests/test_clone.py`

**Interfaces:**
- Produces: `sleuth.ingest.clone.CloneError(Exception)`, `sleuth.ingest.clone.clone_repo(url: str, dest_dir: str) -> Path`, `sleuth.ingest.clone.list_source_files(repo_path: Path, extensions: set[str]) -> list[Path]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clone.py
import subprocess
from pathlib import Path

import pytest

from sleuth.ingest.clone import CloneError, clone_repo, list_source_files


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)

    (repo_dir / "main.py").write_text("def foo():\n    return 1\n")
    (repo_dir / "README.md").write_text("hello\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def test_clone_repo_copies_committed_files(local_git_repo, tmp_path):
    dest = tmp_path / "cloned"

    result = clone_repo(str(local_git_repo), str(dest))

    assert result == dest
    assert (dest / "main.py").exists()


def test_clone_repo_raises_on_invalid_source(tmp_path):
    dest = tmp_path / "cloned"

    with pytest.raises(CloneError):
        clone_repo(str(tmp_path / "does_not_exist"), str(dest))


def test_list_source_files_filters_by_extension(local_git_repo):
    files = list_source_files(local_git_repo, {".py"})

    names = {f.name for f in files}
    assert names == {"main.py"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clone.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.clone'`

- [ ] **Step 3: Write sleuth/ingest/clone.py**

```python
import subprocess
from pathlib import Path


class CloneError(Exception):
    pass


def clone_repo(url: str, dest_dir: str) -> Path:
    dest = Path(dest_dir)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CloneError(result.stderr.strip())
    return dest


def list_source_files(repo_path: Path, extensions: set[str]) -> list[Path]:
    files = []
    for path in Path(repo_path).rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_file() and path.suffix in extensions:
            files.append(path)
    return files
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clone.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/ingest/clone.py tests/test_clone.py
git commit -m "feat: add git clone and source file listing"
```

---

### Task 7: Shared HTTP retry helper + pluggable Embedder (Voyage/NIM, batched, concurrent)

**Files:**
- Create: `sleuth/http_retry.py`
- Create: `sleuth/ingest/embed.py`
- Test: `tests/test_http_retry.py`
- Test: `tests/test_embed.py`

**Interfaces:**
- Produces: `async def sleuth.http_retry.post_with_retry(client: httpx.AsyncClient, url: str, *, retries: int = 1, backoff_seconds: float = 1.0, **kwargs) -> httpx.Response` (retries once on a transient failure — network error or 429/500/502/503/504 — then raises on the response via `raise_for_status()`; a non-transient error status raises immediately with no retry).
- Produces: `sleuth.ingest.embed.Embedder` ABC (`async def embed_batch(self, texts: list[str]) -> list[list[float]]`, class attrs `model_name: str`, `dim: int`); `sleuth.ingest.embed.VoyageEmbedder(api_key, batch_size=128, max_concurrency=5)` (`model_name="voyage-code-3"`, `dim=1024`); `sleuth.ingest.embed.NimEmbedder(api_key, batch_size=128, max_concurrency=5)` (`model_name="nemotron-3-embed-1b"`, `dim=2048`, no output-dimension truncation param); `sleuth.ingest.embed.get_embedder(config: Config) -> Embedder` (picks by `config.embedding_provider`). Both implementations preserve input order and use `post_with_retry` internally.

- [ ] **Step 1: Write the failing test for the retry helper**

```python
# tests/test_http_retry.py
import httpx
import pytest
import respx

from sleuth.http_retry import post_with_retry


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_succeeds_after_one_transient_failure():
    responses = [httpx.Response(503), httpx.Response(200, json={"ok": True})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    respx.post("https://example.test/x").mock(side_effect=handler)

    async with httpx.AsyncClient() as client:
        response = await post_with_retry(client, "https://example.test/x", backoff_seconds=0)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_raises_after_exhausting_retries():
    respx.post("https://example.test/x").mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://example.test/x", retries=1, backoff_seconds=0)

    assert len(respx.calls) == 2  # original attempt + 1 retry


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_does_not_retry_non_transient_error():
    respx.post("https://example.test/x").mock(return_value=httpx.Response(401))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://example.test/x", retries=1, backoff_seconds=0)

    assert len(respx.calls) == 1  # no retry for a non-transient 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_http_retry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.http_retry'`

- [ ] **Step 3: Write sleuth/http_retry.py**

```python
import asyncio

import httpx

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


async def post_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 1, backoff_seconds: float = 1.0, **kwargs
) -> httpx.Response:
    attempt = 0
    while True:
        try:
            response = await client.post(url, **kwargs)
        except httpx.TransportError:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(backoff_seconds)
                continue
            raise

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < retries:
            attempt += 1
            await asyncio.sleep(backoff_seconds)
            continue

        response.raise_for_status()
        return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_http_retry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit the retry helper**

```bash
git add sleuth/http_retry.py tests/test_http_retry.py
git commit -m "feat: add shared HTTP retry helper for transient API failures"
```

- [ ] **Step 6: Write the failing test for the Embedder interface**

```python
# tests/test_embed.py
import json

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.ingest.embed import NimEmbedder, VoyageEmbedder, get_embedder


@pytest.mark.asyncio
@respx.mock
async def test_voyage_embedder_batches_and_preserves_order():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content)
        inputs = body["input"]
        assert body["model"] == "voyage-code-3"
        assert body["output_dimension"] == 1024
        data = [{"embedding": [float(len(text)), 0.0]} for text in inputs]
        return httpx.Response(200, json={"data": data})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="key", batch_size=2, max_concurrency=5)
    result = await embedder.embed_batch(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [vec[0] for vec in result] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert call_count == 3  # batches of 2, 2, 1
    assert embedder.model_name == "voyage-code-3"
    assert embedder.dim == 1024


@pytest.mark.asyncio
@respx.mock
async def test_voyage_embedder_sends_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="secret-key")
    await embedder.embed_batch(["only one"])


@pytest.mark.asyncio
@respx.mock
async def test_nim_embedder_uses_nemotron_model_and_dim():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "nemotron-3-embed-1b"
        assert "output_dimension" not in body
        data = [{"embedding": [0.0] * 2048} for _ in body["input"]]
        return httpx.Response(200, json={"data": data})

    respx.post("https://integrate.api.nvidia.com/v1/embeddings").mock(side_effect=handler)

    embedder = NimEmbedder(api_key="nim-key")
    result = await embedder.embed_batch(["x"])

    assert len(result[0]) == 2048
    assert embedder.model_name == "nemotron-3-embed-1b"
    assert embedder.dim == 2048


def test_get_embedder_picks_by_provider():
    voyage_config = Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="vk", nim_api_key=None, groq_api_key="gk",
        groq_model="m", database_url="unused",
    )
    nim_config = Config(
        embedding_provider="nim", generation_provider="groq",
        voyage_api_key=None, nim_api_key="nk", groq_api_key="gk",
        groq_model="m", database_url="unused",
    )

    assert isinstance(get_embedder(voyage_config), VoyageEmbedder)
    assert isinstance(get_embedder(nim_config), NimEmbedder)
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.embed'`

- [ ] **Step 8: Write sleuth/ingest/embed.py**

```python
import asyncio
from abc import ABC, abstractmethod

import httpx

from sleuth.config import Config
from sleuth.http_retry import post_with_retry

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
NIM_URL = "https://integrate.api.nvidia.com/v1/embeddings"


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class Embedder(ABC):
    model_name: str
    dim: int

    def __init__(self, api_key: str, batch_size: int = 128, max_concurrency: int = 5):
        self.api_key = api_key
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency

    @abstractmethod
    def _url(self) -> str: ...

    @abstractmethod
    def _request_payload(self, batch: list[str]) -> dict: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        batches = _batches(texts, self.batch_size)
        sem = asyncio.Semaphore(self.max_concurrency)

        async with httpx.AsyncClient(timeout=60) as client:
            results = await asyncio.gather(*[self._embed_one_batch(client, b, sem) for b in batches])

        embeddings: list[list[float]] = []
        for batch_result in results:
            embeddings.extend(batch_result)
        return embeddings

    async def _embed_one_batch(
        self, client: httpx.AsyncClient, batch: list[str], sem: asyncio.Semaphore
    ) -> list[list[float]]:
        async with sem:
            response = await post_with_retry(
                client,
                self._url(),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=self._request_payload(batch),
            )
            data = response.json()["data"]
            return [item["embedding"] for item in data]


class VoyageEmbedder(Embedder):
    model_name = "voyage-code-3"
    dim = 1024

    def _url(self) -> str:
        return VOYAGE_URL

    def _request_payload(self, batch: list[str]) -> dict:
        return {"input": batch, "model": self.model_name, "output_dimension": self.dim}


class NimEmbedder(Embedder):
    model_name = "nemotron-3-embed-1b"
    dim = 2048

    def _url(self) -> str:
        return NIM_URL

    def _request_payload(self, batch: list[str]) -> dict:
        # NIM's nemotron embedding model has no output-dimension truncation
        # support (per spec) -- dim is fixed at 2048, no truncation param sent.
        return {"input": batch, "model": self.model_name, "input_type": "passage"}


def get_embedder(config: Config) -> Embedder:
    if config.embedding_provider == "voyage":
        return VoyageEmbedder(api_key=config.voyage_api_key)
    if config.embedding_provider == "nim":
        return NimEmbedder(api_key=config.nim_api_key)
    raise ValueError(f"Unknown embedding provider: {config.embedding_provider}")
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_embed.py -v`
Expected: PASS (4 tests)

- [ ] **Step 10: Commit**

```bash
git add sleuth/ingest/embed.py tests/test_embed.py
git commit -m "feat: add pluggable Embedder interface (Voyage/NIM), batched and concurrent"
```

---

### Task 8: Repo/chunk store (raw SQL CRUD, per-dimension table selection)

**Files:**
- Create: `sleuth/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `sleuth.chunking.Chunk`, `sleuth.db.get_connection`/`apply_schema` (via `pg_conn` fixture).
- Produces: `sleuth.store.create_repo(conn, github_url: str) -> str` (repo id), `sleuth.store.update_repo_status(conn, repo_id: str, status: str, error_message: str | None = None) -> None`, `sleuth.store.set_repo_embedding_info(conn, repo_id: str, model: str, dim: int) -> None`, `sleuth.store.get_existing_hashes(conn, repo_id: str, dim: int) -> dict[tuple[str, str | None], str]`, `sleuth.store.upsert_chunks(conn, repo_id: str, chunks_with_embeddings: list[tuple[Chunk, list[float]]], dim: int) -> None`, `sleuth.store.delete_stale_chunks(conn, repo_id: str, current_keys: set[tuple[str, str | None]], dim: int) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from sleuth.chunking import Chunk
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
)


def test_create_repo_and_update_status(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    row = pg_conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "pending"

    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()

    row = pg_conn.execute("SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "ready"
    assert row[1] is None


def test_set_repo_embedding_info(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    set_repo_embedding_info(pg_conn, repo_id, "voyage-code-3", 1024)
    pg_conn.commit()

    row = pg_conn.execute(
        "SELECT embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "voyage-code-3"
    assert row[1] == 1024


def test_upsert_and_get_existing_hashes(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    chunk_b = Chunk("f.py", None, "module", 3, 3, "X = 1\n")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)],
        dim=1024,
    )
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id, dim=1024)

    assert hashes[("f.py", "foo")] == chunk_a.content_hash
    assert hashes[("f.py", None)] == chunk_b.content_hash


def test_upsert_overwrites_existing_row_on_conflict(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    original = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    upsert_chunks(pg_conn, repo_id, [(original, [0.1] * 1024)], dim=1024)
    pg_conn.commit()

    changed = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 2\n")
    upsert_chunks(pg_conn, repo_id, [(changed, [0.9] * 1024)], dim=1024)
    pg_conn.commit()

    count = pg_conn.execute("SELECT count(*) FROM chunks_1024 WHERE repo_id = %s", (repo_id,)).fetchone()[0]
    assert count == 1

    hashes = get_existing_hashes(pg_conn, repo_id, dim=1024)
    assert hashes[("f.py", "foo")] == changed.content_hash


def test_delete_stale_chunks_removes_missing_keys(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "code a")
    chunk_b = Chunk("g.py", "bar", "function", 1, 2, "code b")
    upsert_chunks(pg_conn, repo_id, [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)], dim=1024)
    pg_conn.commit()

    delete_stale_chunks(pg_conn, repo_id, current_keys={("f.py", "foo")}, dim=1024)
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id, dim=1024)
    assert set(hashes) == {("f.py", "foo")}


def test_upsert_rejects_unsupported_dim(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk = Chunk("f.py", "foo", "function", 1, 2, "code")
    try:
        upsert_chunks(pg_conn, repo_id, [(chunk, [0.1] * 512)], dim=512)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.store'`

- [ ] **Step 3: Write sleuth/store.py**

```python
from sleuth.chunking import Chunk

_CHUNKS_TABLES = {1024: "chunks_1024", 2048: "chunks_2048"}


def _chunks_table(dim: int) -> str:
    table = _CHUNKS_TABLES.get(dim)
    if table is None:
        raise ValueError(f"Unsupported embedding dimension: {dim}")
    return table


def create_repo(conn, github_url: str) -> str:
    row = conn.execute(
        "INSERT INTO repos (github_url, status) VALUES (%s, 'pending') RETURNING id",
        (github_url,),
    ).fetchone()
    return str(row[0])


def update_repo_status(conn, repo_id: str, status: str, error_message: str | None = None) -> None:
    conn.execute(
        """
        UPDATE repos
        SET status = %s,
            error_message = %s,
            indexed_at = CASE WHEN %s = 'ready' THEN now() ELSE indexed_at END
        WHERE id = %s
        """,
        (status, error_message, status, repo_id),
    )


def set_repo_embedding_info(conn, repo_id: str, model: str, dim: int) -> None:
    conn.execute(
        "UPDATE repos SET embedding_model = %s, embedding_dim = %s WHERE id = %s",
        (model, dim, repo_id),
    )


def get_existing_hashes(conn, repo_id: str, dim: int) -> dict[tuple[str, str | None], str]:
    table = _chunks_table(dim)
    rows = conn.execute(
        f"SELECT file_path, symbol_name, content_hash FROM {table} WHERE repo_id = %s",
        (repo_id,),
    ).fetchall()
    return {(file_path, symbol_name): content_hash for file_path, symbol_name, content_hash in rows}


def upsert_chunks(
    conn, repo_id: str, chunks_with_embeddings: list[tuple[Chunk, list[float]]], dim: int
) -> None:
    table = _chunks_table(dim)
    for chunk, embedding in chunks_with_embeddings:
        conn.execute(
            f"""
            INSERT INTO {table}
                (repo_id, file_path, symbol_name, kind, start_line, end_line, code_text, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_id, file_path, COALESCE(symbol_name, ''))
            DO UPDATE SET
                kind = EXCLUDED.kind,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                code_text = EXCLUDED.code_text,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding
            """,
            (
                repo_id,
                chunk.file_path,
                chunk.symbol_name,
                chunk.kind,
                chunk.start_line,
                chunk.end_line,
                chunk.code_text,
                chunk.content_hash,
                embedding,
            ),
        )


def delete_stale_chunks(conn, repo_id: str, current_keys: set[tuple[str, str | None]], dim: int) -> None:
    table = _chunks_table(dim)
    existing = get_existing_hashes(conn, repo_id, dim)
    stale = [key for key in existing if key not in current_keys]
    for file_path, symbol_name in stale:
        conn.execute(
            f"DELETE FROM {table} WHERE repo_id = %s AND file_path = %s AND symbol_name IS NOT DISTINCT FROM %s",
            (repo_id, file_path, symbol_name),
        )
```

`_chunks_table` only ever resolves to one of two hardcoded literals (never user input), so the f-string table name is safe from SQL injection.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/store.py tests/test_store.py
git commit -m "feat: add repo/chunk store with per-dimension tables and incremental upsert"
```

---

### Task 9: Ingest pipeline orchestration (pluggable embedder, model-change re-embed)

**Files:**
- Create: `sleuth/ingest/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `clone_repo`, `list_source_files` (Task 6); `chunk_source` (Task 5); `get_embedder` (Task 7); `format_chunk_context` (Task 3); `create_repo`, `update_repo_status`, `set_repo_embedding_info`, `get_existing_hashes`, `upsert_chunks`, `delete_stale_chunks` (Task 8); `Config` (Task 1).
- Produces: `async def sleuth.ingest.pipeline.ingest_repo(github_url: str, conn, config: Config) -> str` (returns repo_id; never raises — failures land in `repos.status = 'failed'`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import json
import subprocess

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import get_existing_hashes


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("def foo():\n    return 1\n")
    (repo_dir / "b.py").write_text("def bar():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def _voyage_config():
    return Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="k", nim_api_key=None, groq_api_key="k",
        groq_model="m", database_url="unused",
    )


def _mock_voyage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = [{"embedding": [float(len(t))] * 1024} for t in body["input"]]
        return httpx.Response(200, json={"data": data})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_creates_chunks_and_marks_ready(pg_conn, local_git_repo):
    _mock_voyage()
    config = _voyage_config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)

    row = pg_conn.execute("SELECT status, embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "ready"
    assert row[1] == "voyage-code-3"
    assert row[2] == 1024

    hashes = get_existing_hashes(pg_conn, repo_id, dim=1024)
    assert ("a.py", "foo") in hashes
    assert ("b.py", "bar") in hashes


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_skips_unchanged_chunks_on_reindex(pg_conn, local_git_repo):
    _mock_voyage()
    config = _voyage_config()

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)
    hashes_before = get_existing_hashes(pg_conn, repo_id, dim=1024)

    # change only a.py
    (local_git_repo / "a.py").write_text("def foo():\n    return 999\n")
    subprocess.run(["git", "add", "."], cwd=local_git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "change a"], cwd=local_git_repo, check=True, capture_output=True)

    call_count_before = len(respx.calls)
    repo_id_2 = await ingest_repo(str(local_git_repo), pg_conn, config)
    calls_during_reindex = len(respx.calls) - call_count_before

    hashes_after = get_existing_hashes(pg_conn, repo_id_2, dim=1024)

    assert hashes_after[("a.py", "foo")] != hashes_before[("a.py", "foo")]
    assert hashes_after[("b.py", "bar")] == hashes_before[("b.py", "bar")]
    # only the changed chunk (and possibly a module-level chunk if present) got re-embedded
    assert calls_during_reindex >= 1


@pytest.mark.asyncio
async def test_ingest_repo_marks_failed_on_clone_error(pg_conn, tmp_path):
    config = _voyage_config()

    repo_id = await ingest_repo(str(tmp_path / "nope"), pg_conn, config)

    row = pg_conn.execute(
        "SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] is not None


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_forces_full_reembed_on_model_change(pg_conn, local_git_repo):
    _mock_voyage()

    def nim_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = [{"embedding": [float(len(t))] * 2048} for t in body["input"]]
        return httpx.Response(200, json={"data": data})

    respx.post("https://integrate.api.nvidia.com/v1/embeddings").mock(side_effect=nim_handler)

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, _voyage_config())
    row = pg_conn.execute(
        "SELECT embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row == ("voyage-code-3", 1024)

    nim_config = Config(
        embedding_provider="nim", generation_provider="groq",
        voyage_api_key=None, nim_api_key="k", groq_api_key="k",
        groq_model="m", database_url="unused",
    )
    call_count_before = len(respx.calls)
    repo_id_2 = await ingest_repo(str(local_git_repo), pg_conn, nim_config)
    calls_during_reindex = len(respx.calls) - call_count_before

    row = pg_conn.execute(
        "SELECT embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id_2,)
    ).fetchone()
    assert row == ("nemotron-3-embed-1b", 2048)

    hashes_2048 = get_existing_hashes(pg_conn, repo_id_2, dim=2048)
    assert ("a.py", "foo") in hashes_2048
    assert ("b.py", "bar") in hashes_2048
    # both chunks re-embedded against NIM despite unchanged content_hash, since the model changed
    assert calls_during_reindex >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.pipeline'`

- [ ] **Step 3: Write sleuth/ingest/pipeline.py**

```python
import shutil
import tempfile

from sleuth.chunking import format_chunk_context
from sleuth.config import Config
from sleuth.ingest.chunk import chunk_source
from sleuth.ingest.clone import CloneError, clone_repo, list_source_files
from sleuth.ingest.embed import get_embedder
from sleuth.ingest.parse import LANGUAGES
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
)

SUPPORTED_EXTENSIONS = set(LANGUAGES.keys())
EXTENSION_TO_LANGUAGE = {ext: spec.key for ext, spec in LANGUAGES.items()}


def _find_or_create_repo(conn, github_url: str) -> str:
    row = conn.execute("SELECT id FROM repos WHERE github_url = %s", (github_url,)).fetchone()
    if row is not None:
        return str(row[0])
    repo_id = create_repo(conn, github_url)
    conn.commit()
    return repo_id


async def ingest_repo(github_url: str, conn, config: Config) -> str:
    repo_id = _find_or_create_repo(conn, github_url)
    update_repo_status(conn, repo_id, "indexing")
    conn.commit()

    embedder = get_embedder(config)

    workdir = tempfile.mkdtemp(prefix="sleuth-clone-")
    try:
        try:
            repo_path = clone_repo(github_url, workdir)
        except CloneError as exc:
            update_repo_status(conn, repo_id, "failed", str(exc))
            conn.commit()
            return repo_id

        files = list_source_files(repo_path, SUPPORTED_EXTENSIONS)

        all_chunks = []
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                continue  # skip files that fail to parse, don't abort the whole index
            all_chunks.extend(chunks)

        current_keys = {(c.file_path, c.symbol_name) for c in all_chunks}

        row = conn.execute("SELECT embedding_model FROM repos WHERE id = %s", (repo_id,)).fetchone()
        stored_model = row[0] if row else None
        model_changed = stored_model is not None and stored_model != embedder.model_name

        # A model change invalidates every content_hash skip for this repo --
        # treat existing hashes as empty so everything gets re-embedded.
        existing_hashes = {} if model_changed else get_existing_hashes(conn, repo_id, embedder.dim)

        to_embed = [
            c for c in all_chunks
            if existing_hashes.get((c.file_path, c.symbol_name)) != c.content_hash
        ]

        if to_embed:
            texts = [
                format_chunk_context(c, EXTENSION_TO_LANGUAGE.get("." + c.file_path.rsplit(".", 1)[-1], ""))
                for c in to_embed
            ]
            vectors = await embedder.embed_batch(texts)
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)), dim=embedder.dim)
            conn.commit()

        set_repo_embedding_info(conn, repo_id, embedder.model_name, embedder.dim)
        conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys, dim=embedder.dim)
        conn.commit()

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

Known limitation, acceptable for this plan's scope: switching a repo's embedding model leaves its old rows behind in the *previous* dimension's table (e.g. `chunks_1024` rows survive a switch to NIM/`chunks_2048`) since `delete_stale_chunks` only ever touches the table matching the *current* dim. They're inert (no code path reads a repo's chunks from any table but the one matching its current `embedding_dim`) but not reclaimed. A cleanup pass (delete-all from the old table on model change) is a small addition if this matters later — not built here since v2 doesn't call it out as required.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose up -d && sleep 2 && pytest tests/test_pipeline.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: add ingest pipeline orchestration with pluggable embedder and model-change re-embed"
```

---

### Task 10: Vector search (per-dimension table selection)

**Files:**
- Create: `sleuth/retrieve/__init__.py`
- Create: `sleuth/retrieve/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `Chunk` (Task 3), `pg_conn` fixture.
- Produces: `sleuth.retrieve.search.SearchResult` dataclass (`file_path`, `symbol_name`, `kind`, `start_line`, `end_line`, `code_text`, `distance: float`), `sleuth.retrieve.search.search_chunks(conn, repo_id: str, query_embedding: list[float], dim: int, top_k: int = 8) -> list[SearchResult]`.

- [ ] **Step 1: Create sleuth/retrieve/__init__.py (empty)**

```python
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_search.py
from sleuth.chunking import Chunk
from sleuth.retrieve.search import search_chunks
from sleuth.store import create_repo, upsert_chunks


def _one_hot(dim: int, size: int = 1024) -> list[float]:
    vec = [0.0] * size
    vec[dim] = 1.0
    return vec


def test_search_chunks_orders_by_similarity(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "def foo(): pass")
    chunk_b = Chunk("g.py", "bar", "function", 1, 2, "def bar(): pass")
    chunk_c = Chunk("h.py", "baz", "function", 1, 2, "def baz(): pass")

    upsert_chunks(
        pg_conn,
        repo_id,
        [
            (chunk_a, _one_hot(0)),
            (chunk_b, _one_hot(1)),
            (chunk_c, _one_hot(2)),
        ],
        dim=1024,
    )
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, _one_hot(1), dim=1024, top_k=2)

    assert len(results) == 2
    assert results[0].symbol_name == "bar"
    assert results[0].distance < results[1].distance


def test_search_chunks_scoped_to_repo_id(pg_conn):
    repo_a = create_repo(pg_conn, "https://github.com/example/repo-a")
    repo_b = create_repo(pg_conn, "https://github.com/example/repo-b")
    pg_conn.commit()

    upsert_chunks(pg_conn, repo_a, [(Chunk("f.py", "foo", "function", 1, 2, "code"), _one_hot(0))], dim=1024)
    upsert_chunks(pg_conn, repo_b, [(Chunk("g.py", "bar", "function", 1, 2, "code"), _one_hot(0))], dim=1024)
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_a, _one_hot(0), dim=1024, top_k=10)

    assert len(results) == 1
    assert results[0].symbol_name == "foo"


def test_search_chunks_rejects_unsupported_dim(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    try:
        search_chunks(pg_conn, repo_id, [0.0] * 512, dim=512)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.retrieve.search'`

- [ ] **Step 4: Write sleuth/retrieve/search.py**

```python
from dataclasses import dataclass

_CHUNKS_TABLES = {1024: "chunks_1024", 2048: "chunks_2048"}


def _chunks_table(dim: int) -> str:
    table = _CHUNKS_TABLES.get(dim)
    if table is None:
        raise ValueError(f"Unsupported embedding dimension: {dim}")
    return table


@dataclass
class SearchResult:
    file_path: str
    symbol_name: str | None
    kind: str
    start_line: int
    end_line: int
    code_text: str
    distance: float


def search_chunks(
    conn, repo_id: str, query_embedding: list[float], dim: int, top_k: int = 8
) -> list[SearchResult]:
    table = _chunks_table(dim)
    rows = conn.execute(
        f"""
        SELECT file_path, symbol_name, kind, start_line, end_line, code_text,
               embedding <=> %s AS distance
        FROM {table}
        WHERE repo_id = %s
        ORDER BY distance ASC
        LIMIT %s
        """,
        (query_embedding, repo_id, top_k),
    ).fetchall()

    return [
        SearchResult(
            file_path=r[0],
            symbol_name=r[1],
            kind=r[2],
            start_line=r[3],
            end_line=r[4],
            code_text=r[5],
            distance=float(r[6]),
        )
        for r in rows
    ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_search.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add sleuth/retrieve/__init__.py sleuth/retrieve/search.py tests/test_search.py
git commit -m "feat: add pgvector similarity search over per-dimension tables"
```

---

### Task 11: Pluggable Generator (Groq/NIM, streaming, fallback) + answer generation

**Files:**
- Create: `sleuth/llm/__init__.py`
- Create: `sleuth/llm/generate.py`
- Create: `sleuth/retrieve/answer.py`
- Test: `tests/test_generate.py`
- Test: `tests/test_answer.py`

**Interfaces:**
- Produces: `sleuth.llm.generate.Generator` ABC (`async def chat(self, messages, stream=True) -> AsyncIterator[str]`, class attr `model_name: str`); `sleuth.llm.generate.GroqGenerator(api_key, model_name=None)`; `sleuth.llm.generate.NimGenerator(api_key, model_name=None)`; `sleuth.llm.generate.get_generator(config: Config) -> Generator`; `sleuth.llm.generate.get_fallback_chain(config: Config) -> list[Generator]` (Groq primary + NIM as failover, only if `nim_api_key` is set); `async def sleuth.llm.generate.chat_with_fallback(chain: list[Generator], messages, stream=True) -> AsyncIterator[str]` (tries each generator in order, one retry-with-backoff already happens inside `post_with_retry` per generator, failing over to the next generator in the chain on a persistent transient error; raises `RuntimeError` only if the whole chain fails).
- Produces: `sleuth.retrieve.answer.build_prompt(question: str, results: list[SearchResult]) -> str` (unchanged), `async def sleuth.retrieve.answer.stream_answer(question: str, repo_id: str, conn, config: Config) -> AsyncIterator[str]` (raises `ValueError` if `repos.status != 'ready'`; embeds the question with the **repo's recorded embedding model**, not the current config default), `async def sleuth.retrieve.answer.get_answer(question: str, repo_id: str, conn, config: Config) -> str` (thin wrapper joining `stream_answer`'s tokens, for callers/tests that want one string).

- [ ] **Step 1: Write the failing test for the Generator interface**

```python
# tests/test_generate.py
import json

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.llm.generate import GroqGenerator, NimGenerator, chat_with_fallback, get_fallback_chain, get_generator


@pytest.mark.asyncio
@respx.mock
async def test_groq_generator_streams_tokens():
    sse = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse.encode())
    )

    generator = GroqGenerator(api_key="k", model_name="test-model")
    tokens = [t async for t in generator.chat([{"role": "user", "content": "hi"}], stream=True)]

    assert "".join(tokens) == "Hello world"


@pytest.mark.asyncio
@respx.mock
async def test_groq_generator_non_streaming_returns_full_text():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "full answer"}}]})

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=handler)

    generator = GroqGenerator(api_key="k", model_name="test-model")
    tokens = [t async for t in generator.chat([{"role": "user", "content": "hi"}], stream=False)]

    assert tokens == ["full answer"]


@pytest.mark.asyncio
@respx.mock
async def test_chat_with_fallback_fails_over_to_nim_on_persistent_groq_failure():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(return_value=httpx.Response(429))
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "nim answer"}}]})
    )

    chain = [GroqGenerator(api_key="k", model_name="m"), NimGenerator(api_key="k")]
    tokens = [
        t async for t in chat_with_fallback(chain, [{"role": "user", "content": "hi"}], stream=False)
    ]

    assert tokens == ["nim answer"]


def test_get_generator_and_fallback_chain():
    config = Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="vk", nim_api_key="nk", groq_api_key="gk",
        groq_model="test-model", database_url="unused",
    )

    generator = get_generator(config)
    assert isinstance(generator, GroqGenerator)
    assert generator.model_name == "test-model"

    chain = get_fallback_chain(config)
    assert isinstance(chain[0], GroqGenerator)
    assert isinstance(chain[1], NimGenerator)


def test_get_fallback_chain_without_nim_key_is_primary_only():
    config = Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="vk", nim_api_key=None, groq_api_key="gk",
        groq_model="test-model", database_url="unused",
    )

    chain = get_fallback_chain(config)
    assert len(chain) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.llm'`

- [ ] **Step 3: Create sleuth/llm/__init__.py (empty)**

```python
```

- [ ] **Step 4: Write sleuth/llm/generate.py**

```python
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from sleuth.config import Config
from sleuth.http_retry import post_with_retry

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class Generator(ABC):
    model_name: str

    def __init__(self, api_key: str, model_name: str | None = None):
        self.api_key = api_key
        if model_name:
            self.model_name = model_name

    @abstractmethod
    def _url(self) -> str: ...

    async def chat(self, messages: list[dict], stream: bool = True) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=60) as client:
            if stream:
                async for token in self._stream_chat(client, messages):
                    yield token
            else:
                yield await self._chat_once(client, messages)

    async def _chat_once(self, client: httpx.AsyncClient, messages: list[dict]) -> str:
        response = await post_with_retry(
            client,
            self._url(),
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_name, "messages": messages},
        )
        return response.json()["choices"][0]["message"]["content"]

    async def _stream_chat(self, client: httpx.AsyncClient, messages: list[dict]) -> AsyncIterator[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model_name, "messages": messages, "stream": True}
        async with client.stream("POST", self._url(), headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta


class GroqGenerator(Generator):
    model_name = "llama-3.3-70b-versatile"

    def _url(self) -> str:
        return GROQ_URL


class NimGenerator(Generator):
    model_name = "meta/llama-3.1-70b-instruct"

    def _url(self) -> str:
        return NIM_URL


def get_generator(config: Config) -> Generator:
    if config.generation_provider == "groq":
        return GroqGenerator(api_key=config.groq_api_key, model_name=config.groq_model)
    if config.generation_provider == "nim":
        return NimGenerator(api_key=config.nim_api_key)
    raise ValueError(f"Unknown generation provider: {config.generation_provider}")


def get_fallback_chain(config: Config) -> list[Generator]:
    chain = [get_generator(config)]
    if config.generation_provider == "groq" and config.nim_api_key:
        chain.append(NimGenerator(api_key=config.nim_api_key))
    return chain


async def chat_with_fallback(
    chain: list[Generator], messages: list[dict], stream: bool = True
) -> AsyncIterator[str]:
    last_error: Exception | None = None
    for generator in chain:
        try:
            async for token in generator.chat(messages, stream=stream):
                yield token
            return
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"All generators in fallback chain failed: {last_error}")
```

Note: `post_with_retry`'s one retry-with-backoff happens *inside* each generator's `_chat_once`/`_stream_chat` call — `chat_with_fallback`'s loop is the second layer (failover to the next provider) described in the spec's fallback chain. A failure that happens mid-stream (after some tokens were already yielded) isn't retried into the next generator — only a failure on the initial request (a non-2xx status before any body is read) triggers failover, which covers the common case (429/5xx returned immediately).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_generate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit the Generator interface**

```bash
git add sleuth/llm/__init__.py sleuth/llm/generate.py tests/test_generate.py
git commit -m "feat: add pluggable Generator (Groq/NIM) with streaming and fallback chain"
```

- [ ] **Step 7: Write the failing test for answer generation**

```python
# tests/test_answer.py
import json

import httpx
import pytest
import respx

from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.retrieve.answer import build_prompt, get_answer
from sleuth.retrieve.search import SearchResult
from sleuth.store import create_repo, set_repo_embedding_info, update_repo_status, upsert_chunks


def test_build_prompt_includes_question_and_chunks():
    results = [
        SearchResult("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n", 0.1),
        SearchResult("g.py", None, "module", 1, 1, "X = 1\n", 0.2),
    ]

    prompt = build_prompt("What does foo do?", results)

    assert "What does foo do?" in prompt
    assert "f.py" in prompt
    assert "foo" in prompt
    assert "def foo():" in prompt
    assert "g.py" in prompt
    assert "X = 1" in prompt


@pytest.mark.asyncio
@respx.mock
async def test_get_answer_rejects_repo_not_ready(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()  # status defaults to 'pending'
    config = Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="k", nim_api_key=None, groq_api_key="k",
        groq_model="m", database_url="unused",
    )

    with pytest.raises(ValueError, match="not ready"):
        await get_answer("question?", repo_id, pg_conn, config)

    assert len(respx.calls) == 0


@pytest.mark.asyncio
@respx.mock
async def test_get_answer_uses_repos_recorded_model_not_config_default(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    set_repo_embedding_info(pg_conn, repo_id, "voyage-code-3", 1024)
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
        dim=1024,
    )
    pg_conn.commit()

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert any("foo" in m["content"] for m in body["messages"])
        sse = 'data: {"choices":[{"delta":{"content":"foo returns 1."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    # config's embedding_provider is nim, but the repo was indexed with voyage --
    # the question must still be embedded with voyage (the repo's recorded model)
    config = Config(
        embedding_provider="nim", generation_provider="groq",
        voyage_api_key="k", nim_api_key="k", groq_api_key="k",
        groq_model="test-model", database_url="unused",
    )
    answer = await get_answer("What does foo do?", repo_id, pg_conn, config)

    assert answer == "foo returns 1."
    voyage_calls = [c for c in respx.calls if "voyageai" in str(c.request.url)]
    assert len(voyage_calls) == 1
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pytest tests/test_answer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.retrieve.answer'`

- [ ] **Step 9: Write sleuth/retrieve/answer.py**

```python
from collections.abc import AsyncIterator

from sleuth.config import Config
from sleuth.ingest.embed import NimEmbedder, VoyageEmbedder
from sleuth.llm.generate import chat_with_fallback, get_fallback_chain
from sleuth.retrieve.search import SearchResult, search_chunks

SYSTEM_PROMPT = (
    "You are a code assistant. Answer the user's question about the repository "
    "using only the provided code excerpts. If the excerpts don't contain the "
    "answer, say so explicitly rather than guessing."
)

_EMBEDDER_BY_MODEL = {
    VoyageEmbedder.model_name: lambda config: VoyageEmbedder(api_key=config.voyage_api_key),
    NimEmbedder.model_name: lambda config: NimEmbedder(api_key=config.nim_api_key),
}


def _resolve_embedder(model_name: str, config: Config):
    factory = _EMBEDDER_BY_MODEL.get(model_name)
    if factory is None:
        raise ValueError(f"Unknown embedding model recorded on repo: {model_name}")
    return factory(config)


def build_prompt(question: str, results: list[SearchResult]) -> str:
    blocks = []
    for r in results:
        symbol = r.symbol_name or "(module level)"
        blocks.append(
            f"# File: {r.file_path}\n# {r.kind}: {symbol} (lines {r.start_line}-{r.end_line})\n\n{r.code_text}"
        )
    context = "\n\n---\n\n".join(blocks)
    return f"Question: {question}\n\nRelevant code:\n\n{context}"


async def stream_answer(question: str, repo_id: str, conn, config: Config) -> AsyncIterator[str]:
    row = conn.execute(
        "SELECT status, embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    if row is None or row[0] != "ready":
        raise ValueError(f"Repo {repo_id} is not ready to query (status={row[0] if row else 'missing'})")
    _, embedding_model, embedding_dim = row

    # Always embed the question with the model the repo was actually indexed
    # with, not the current config default -- a repo can only change models
    # via a full re-index (per spec's query-time model-mismatch rule).
    embedder = _resolve_embedder(embedding_model, config)
    query_vector = (await embedder.embed_batch([question]))[0]
    results = search_chunks(conn, repo_id, query_vector, dim=embedding_dim)
    prompt = build_prompt(question, results)

    chain = get_fallback_chain(config)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    async for token in chat_with_fallback(chain, messages, stream=True):
        yield token


async def get_answer(question: str, repo_id: str, conn, config: Config) -> str:
    return "".join([token async for token in stream_answer(question, repo_id, conn, config)])
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/test_answer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 11: Commit**

```bash
git add sleuth/retrieve/answer.py tests/test_answer.py
git commit -m "feat: add streaming answer generation using the repo's recorded embedding model"
```

---

### Task 12: CLI (add / list / ask / agentic / eval)

**Files:**
- Create: `sleuth/cli.py`
- Create: `sleuth/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ingest_repo` (Task 9), `stream_answer` (Task 11), `get_connection`/`apply_schema` (Task 2), `load_config` (Task 1), `run_agentic` (Task 13 — wired here, implemented next), `run_eval` (Task 14 — wired here, implemented after).
- Produces: `sleuth.cli.main(argv: list[str] | None = None) -> None`.

Note: this task wires the `agentic` and `eval` subcommands against modules that don't exist yet (`sleuth.retrieve.agentic.run_agentic`, `sleuth.eval.runner.run_eval`) — Tasks 13 and 14 build them next. `sleuth/cli.py` imports them by name, so the CLI's own test suite (Step 2 below) only exercises `add`/`list`/`ask` for real and smoke-tests `agentic`/`eval` by monkeypatching those two functions; a full pass of `pytest tests/test_cli.py` therefore requires Tasks 13 and 14's modules to exist (even minimally) before it's green — do Tasks 12, 13, 14 back-to-back.

- [ ] **Step 1: Write sleuth/__main__.py**

`sleuth.cli.main()` is async (it awaits the pipeline/retrieval calls directly, which is what the tests in Step 2 call), so the entry point needs a sync wrapper — that wrapper (`run()`) is defined inside `sleuth/cli.py` in Step 4 below and imported here:

```python
from sleuth.cli import run

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_cli.py
import json
import subprocess

import httpx
import pytest
import respx

from sleuth.cli import main
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("def foo():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


@pytest.mark.asyncio
@respx.mock
async def test_cli_add_list_ask_end_to_end(pg_conn, local_git_repo, monkeypatch, capsys):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024} for _ in body["input"]]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        sse = 'data: {"choices":[{"delta":{"content":"foo returns 1."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode())

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    await main(["add", str(local_git_repo)])
    out = capsys.readouterr().out
    assert "ready" in out

    await main(["list"])
    out = capsys.readouterr().out
    assert str(local_git_repo) in out

    row = pg_conn.execute("SELECT id FROM repos WHERE github_url = %s", (str(local_git_repo),)).fetchone()
    repo_id = str(row[0])

    await main(["ask", repo_id, "What does foo do?"])
    out = capsys.readouterr().out
    assert "foo returns 1." in out


@pytest.mark.asyncio
async def test_cli_agentic_smoke(monkeypatch, capsys, pg_conn, tmp_path):
    async def fake_run_agentic(question, path, config):
        yield "stub agentic answer"

    monkeypatch.setattr("sleuth.cli.run_agentic", fake_run_agentic)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    await main(["agentic", str(tmp_path), "what does this do?"])
    out = capsys.readouterr().out
    assert "stub agentic answer" in out


@pytest.mark.asyncio
async def test_cli_eval_smoke(monkeypatch, capsys, pg_conn, tmp_path):
    async def fake_run_eval(golden_yaml_path, conn, config):
        return "stub eval table"

    monkeypatch.setattr("sleuth.cli.run_eval", fake_run_eval)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text("repo: x\ncases: []\n")

    await main(["eval", str(golden_path)])
    out = capsys.readouterr().out
    assert "stub eval table" in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.cli'`

- [ ] **Step 4: Write sleuth/cli.py**

```python
import argparse
import asyncio

from sleuth.config import load_config
from sleuth.db import apply_schema, get_connection
from sleuth.eval.runner import run_eval
from sleuth.ingest.pipeline import ingest_repo
from sleuth.retrieve.agentic import run_agentic
from sleuth.retrieve.answer import stream_answer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sleuth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Clone and index a repo")
    add_parser.add_argument("github_url")

    subparsers.add_parser("list", help="List indexed repos")

    ask_parser = subparsers.add_parser("ask", help="Ask a question about an indexed repo")
    ask_parser.add_argument("repo_id")
    ask_parser.add_argument("question")

    agentic_parser = subparsers.add_parser(
        "agentic", help="Ask a question about a local directory, live -- no indexing wait"
    )
    agentic_parser.add_argument("path", nargs="?", default=".")
    agentic_parser.add_argument("question")

    eval_parser = subparsers.add_parser("eval", help="Run the retrieval/answer-quality eval harness")
    eval_parser.add_argument("golden_yaml_path")

    return parser


async def _run(args: argparse.Namespace) -> None:
    config = load_config()
    conn = get_connection(config.database_url)
    apply_schema(conn)

    if args.command == "add":
        repo_id = await ingest_repo(args.github_url, conn, config)
        status = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()[0]
        print(f"repo_id={repo_id} status={status}")

    elif args.command == "list":
        rows = conn.execute("SELECT id, github_url, status FROM repos ORDER BY github_url").fetchall()
        for repo_id, github_url, status in rows:
            print(f"{repo_id}  {status:10s}  {github_url}")

    elif args.command == "ask":
        async for token in stream_answer(args.question, args.repo_id, conn, config):
            print(token, end="", flush=True)
        print()

    elif args.command == "agentic":
        async for token in run_agentic(args.question, args.path, config):
            print(token, end="", flush=True)
        print()

    elif args.command == "eval":
        table = await run_eval(args.golden_yaml_path, conn, config)
        print(table)

    conn.close()


async def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    await _run(args)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run test to verify it passes** (after Tasks 13 and 14 exist)

Run: `docker compose up -d && sleep 2 && pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add sleuth/cli.py sleuth/__main__.py tests/test_cli.py
git commit -m "feat: add CLI (add/list/ask/agentic/eval) wiring pipeline, retrieval, and eval together"
```

---

### Task 13: Agentic/live retrieval mode

**Files:**
- Create: `sleuth/retrieve/agentic.py`
- Test: `tests/test_agentic.py`

**Interfaces:**
- Consumes: `Generator`, `get_generator` (Task 11); `Config` (Task 1).
- Produces: `async def sleuth.retrieve.agentic.run_agentic(question: str, path: str, config: Config, generator=None) -> AsyncIterator[str]` (the `generator` param defaults to `get_generator(config)` and exists purely so tests can inject a fake `Generator`).

Implements the spec's Mode 2 (live/agentic): three hand-written tools operating on a local directory (`grep`, `list_files`, `read_file`), a text-protocol tool-call loop (the model responds either with a single `TOOL: <name> {json args}` line or with plain prose as its final answer), capped at 6 iterations, with `grep` capped at 50 matches and `read_file` capped at 400 lines per call. On a non-tool-call response the loop terminates immediately and yields that response as the answer. On hitting the iteration cap, one final forced turn asks the model to answer with whatever's been gathered, and the yielded answer is suffixed with a note that the search was cut short.

Design note: tool-selection turns are non-streaming (`stream=False`) since the loop needs the complete text to decide whether it's a tool call before anything is shown to the user — only the model's *final* answer text is what gets yielded/printed. This is simpler than a two-phase "decide, then re-ask with streaming" design and still satisfies the spec's "streamed to terminal" requirement at the CLI layer (Task 12's `agentic` subcommand prints each yielded chunk as it arrives) — it just means the agentic answer arrives as one chunk rather than token-by-token, unlike indexed-mode `ask` (Task 11), which streams for real. Calling this out explicitly rather than pretending it's true token streaming.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agentic.py
import pytest

from sleuth.retrieve.agentic import run_agentic


class FakeGenerator:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(messages)
        yield self.responses.pop(0)


@pytest.mark.asyncio
async def test_run_agentic_terminates_immediately_on_non_tool_response(tmp_path):
    fake = FakeGenerator(["Direct answer, no tools needed"])

    result = "".join([t async for t in run_agentic("what is this?", str(tmp_path), config=None, generator=fake)])

    assert result == "Direct answer, no tools needed"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_run_agentic_dispatches_list_files_tool_then_answers(tmp_path):
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n")

    fake = FakeGenerator(
        [
            'TOOL: list_files {"glob": "*.py"}',
            "Found main.py, it defines foo.",
        ]
    )

    result = "".join([t async for t in run_agentic("where is foo?", str(tmp_path), config=None, generator=fake)])

    assert result == "Found main.py, it defines foo."
    assert len(fake.calls) == 2
    tool_result_message = fake.calls[1][-1]["content"]
    assert "main.py" in tool_result_message


@pytest.mark.asyncio
async def test_run_agentic_enforces_grep_match_cap(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"# match {i}" for i in range(100)))

    fake = FakeGenerator(
        [
            'TOOL: grep {"pattern": "match"}',
            "done",
        ]
    )

    await "".join([t async for t in run_agentic("find matches", str(tmp_path), config=None, generator=fake)])  # noqa: F841 -- exercised for side effect

    tool_result_message = fake.calls[1][-1]["content"]
    match_lines = [line for line in tool_result_message.splitlines() if "# match" in line]
    assert len(match_lines) <= 50


@pytest.mark.asyncio
async def test_run_agentic_enforces_read_file_line_cap(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"line {i}" for i in range(1000)))

    fake = FakeGenerator(
        [
            'TOOL: read_file {"path": "big.py"}',
            "done",
        ]
    )

    "".join([t async for t in run_agentic("read the file", str(tmp_path), config=None, generator=fake)])

    tool_result_message = fake.calls[1][-1]["content"]
    assert len(tool_result_message.splitlines()) <= 400


@pytest.mark.asyncio
async def test_run_agentic_hits_iteration_cap_and_notes_cut_short(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    responses = ['TOOL: list_files {"glob": "*.py"}'] * 6 + ["forced final answer"]
    fake = FakeGenerator(responses)

    result = "".join([t async for t in run_agentic("q", str(tmp_path), config=None, generator=fake)])

    assert "forced final answer" in result
    assert "cut short" in result.lower()
    assert len(fake.calls) == 7  # 6 tool iterations + 1 forced final turn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agentic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.retrieve.agentic'`

- [ ] **Step 3: Write sleuth/retrieve/agentic.py**

```python
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from sleuth.config import Config
from sleuth.llm.generate import get_generator

MAX_ITERATIONS = 6
GREP_MAX_MATCHES = 50
READ_FILE_MAX_LINES = 400

SYSTEM_PROMPT = (
    "You are a code assistant investigating a local codebase to answer the user's "
    "question. You have three tools:\n"
    "  grep(pattern, glob=null) -- regex search across files, first 50 matches\n"
    "  list_files(glob) -- list files matching a glob\n"
    "  read_file(path, start_line=null, end_line=null) -- read up to 400 lines of a file\n"
    "To call a tool, respond with EXACTLY one line in this form and nothing else:\n"
    'TOOL: <tool_name> {"arg": "value", ...}\n'
    "When you have enough information to answer, respond with your final answer as "
    "plain prose (no TOOL: prefix)."
)

_TOOL_LINE_RE = re.compile(r"^TOOL:\s*(\w+)\s*(\{.*\})\s*$", re.DOTALL)


def _parse_tool_call(text: str) -> tuple[str, dict] | None:
    match = _TOOL_LINE_RE.match(text.strip())
    if not match:
        return None
    try:
        args = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    return match.group(1), args


def _tool_grep(root: Path, pattern: str, glob: str | None = None) -> str:
    regex = re.compile(pattern)
    matches = []
    paths = sorted(root.rglob(glob or "*"))
    for path in paths:
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
                if len(matches) >= GREP_MAX_MATCHES:
                    return "\n".join(matches) + "\n... (truncated at 50 matches)"
    return "\n".join(matches) if matches else "(no matches)"


def _tool_list_files(root: Path, glob: str) -> str:
    paths = sorted(p for p in root.rglob(glob) if p.is_file() and ".git" not in p.parts)
    return "\n".join(str(p.relative_to(root)) for p in paths) or "(no files matched)"


def _tool_read_file(root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    target = root / path
    try:
        lines = target.read_text(errors="ignore").splitlines()
    except OSError as exc:
        return f"(error reading {path}: {exc})"

    start = max((start_line or 1) - 1, 0)
    end = min(end_line or len(lines), start + READ_FILE_MAX_LINES, len(lines))
    snippet = lines[start:end]
    return "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(snippet))


def _dispatch_tool(name: str, args: dict, root: Path) -> str:
    if name == "grep":
        return _tool_grep(root, args.get("pattern", ""), args.get("glob"))
    if name == "list_files":
        return _tool_list_files(root, args.get("glob", "*"))
    if name == "read_file":
        return _tool_read_file(root, args.get("path", ""), args.get("start_line"), args.get("end_line"))
    return f"(unknown tool: {name})"


async def _call(generator, messages: list[dict]) -> str:
    return "".join([t async for t in generator.chat(messages, stream=False)])


async def run_agentic(question: str, path: str, config: Config, generator=None) -> AsyncIterator[str]:
    generator = generator or get_generator(config)
    root = Path(path)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_ITERATIONS):
        response_text = await _call(generator, messages)
        parsed = _parse_tool_call(response_text)

        if parsed is None:
            yield response_text
            return

        messages.append({"role": "assistant", "content": response_text})
        name, args = parsed
        result = _dispatch_tool(name, args, root)
        messages.append({"role": "user", "content": f"Tool result:\n{result}"})

    messages.append(
        {
            "role": "user",
            "content": "Iteration limit reached. Answer the original question with what "
            "you've gathered so far, as plain prose.",
        }
    )
    response_text = await _call(generator, messages)
    yield response_text + "\n\n(Note: search was cut short after reaching the iteration limit.)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agentic.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/retrieve/agentic.py tests/test_agentic.py
git commit -m "feat: add agentic/live retrieval mode (grep/list_files/read_file tool loop)"
```

---

### Task 14: Eval harness

**Files:**
- Create: `sleuth/eval/__init__.py`
- Create: `sleuth/eval/runner.py`
- Create: `eval/sample_repo.yaml` (example golden set for manual `sleuth eval` runs)
- Create: `tests/fixtures/sample_golden.yaml`
- Test: `tests/test_eval_runner.py`

**Interfaces:**
- Produces: `sleuth.eval.runner.GoldenCase` dataclass (`question`, `expected_files`, `expected_symbols`, `reference_answer`), `sleuth.eval.runner.load_golden(path: str) -> tuple[str, list[GoldenCase]]`, `async def sleuth.eval.runner.run_eval(golden_yaml_path: str, conn, config: Config) -> str` (prints/returns a results table with per-case hit/reciprocal-rank/judge-score plus aggregate hit-rate@k, MRR, and average judge score).

- [ ] **Step 1: Write tests/fixtures/sample_golden.yaml**

```yaml
repo: example-repo
cases:
  - question: "Where is foo defined?"
    expected_files:
      - f.py
    expected_symbols:
      - foo
    reference_answer: "foo is defined in f.py and returns 1."
```

- [ ] **Step 2: Write eval/sample_repo.yaml**

```yaml
# Example golden set for `sleuth eval`. Replace `repo` with a real repo_id
# from `sleuth list` after indexing a repo with `sleuth add`.
repo: REPLACE_WITH_REPO_ID
cases:
  - question: "How does the Session class handle cookies?"
    expected_files:
      - requests/sessions.py
    expected_symbols:
      - Session
    reference_answer: >
      The Session class stores cookies in a cookiejar and merges them with
      per-request cookies before sending.
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_eval_runner.py
import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from sleuth.chunking import Chunk
from sleuth.config import Config
from sleuth.eval.runner import load_golden, run_eval
from sleuth.store import create_repo, set_repo_embedding_info, update_repo_status, upsert_chunks

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_golden.yaml"


def test_load_golden_parses_cases():
    repo, cases = load_golden(str(FIXTURE_PATH))

    assert repo == "example-repo"
    assert len(cases) == 1
    assert cases[0].question == "Where is foo defined?"
    assert cases[0].expected_files == ["f.py"]
    assert cases[0].expected_symbols == ["foo"]


@pytest.mark.asyncio
@respx.mock
async def test_run_eval_computes_hit_rate_mrr_and_judge_score(pg_conn, tmp_path):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    set_repo_embedding_info(pg_conn, repo_id, "voyage-code-3", 1024)
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
        dim=1024,
    )
    pg_conn.commit()

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(
        yaml.dump(
            {
                "repo": repo_id,
                "cases": [
                    {
                        "question": "Where is foo defined?",
                        "expected_files": ["f.py"],
                        "expected_symbols": ["foo"],
                        "reference_answer": "foo is defined in f.py and returns 1.",
                    }
                ],
            }
        )
    )

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        if "Score how well" in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "5"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "foo is defined in f.py."}}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(
        embedding_provider="voyage", generation_provider="groq",
        voyage_api_key="k", nim_api_key=None, groq_api_key="k",
        groq_model="test-model", database_url="unused",
    )

    table = await run_eval(str(golden_path), pg_conn, config)

    assert "hit-rate@8: 1.00" in table
    assert "MRR: 1.00" in table
    assert "avg judge: 5.0" in table
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_eval_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.eval'`

- [ ] **Step 5: Create sleuth/eval/__init__.py (empty)**

```python
```

- [ ] **Step 6: Write sleuth/eval/runner.py**

```python
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sleuth.config import Config
from sleuth.ingest.embed import NimEmbedder, VoyageEmbedder
from sleuth.llm.generate import chat_with_fallback, get_fallback_chain, get_generator
from sleuth.retrieve.answer import build_prompt
from sleuth.retrieve.search import search_chunks

TOP_K = 8

_EMBEDDER_BY_MODEL = {
    VoyageEmbedder.model_name: lambda config: VoyageEmbedder(api_key=config.voyage_api_key),
    NimEmbedder.model_name: lambda config: NimEmbedder(api_key=config.nim_api_key),
}

JUDGE_PROMPT = (
    "You are grading a code-assistant answer against a reference answer. "
    "Score how well the produced answer matches the reference on a scale of 1-5 "
    "(5 = fully correct and complete, 1 = wrong or unrelated). "
    "Respond with ONLY the digit.\n\n"
    "Reference answer:\n{reference}\n\nProduced answer:\n{produced}"
)


@dataclass
class GoldenCase:
    question: str
    expected_files: list[str]
    expected_symbols: list[str] = field(default_factory=list)
    reference_answer: str = ""


@dataclass
class CaseResult:
    question: str
    hit: bool
    reciprocal_rank: float
    judge_score: int | None
    answer: str


def load_golden(path: str) -> tuple[str, list[GoldenCase]]:
    data = yaml.safe_load(Path(path).read_text())
    cases = [
        GoldenCase(
            question=c["question"],
            expected_files=c.get("expected_files", []),
            expected_symbols=c.get("expected_symbols", []),
            reference_answer=c.get("reference_answer", ""),
        )
        for c in data.get("cases", [])
    ]
    return data["repo"], cases


def _resolve_embedder(model_name: str, config: Config):
    factory = _EMBEDDER_BY_MODEL.get(model_name)
    if factory is None:
        raise ValueError(f"Unknown embedding model recorded on repo: {model_name}")
    return factory(config)


def _hit_and_rank(results, case: GoldenCase) -> tuple[bool, float]:
    for rank, r in enumerate(results, start=1):
        file_hit = r.file_path in case.expected_files
        symbol_hit = bool(case.expected_symbols) and r.symbol_name in case.expected_symbols
        if file_hit or symbol_hit:
            return True, 1.0 / rank
    return False, 0.0


def _parse_judge_score(text: str) -> int | None:
    match = re.search(r"[1-5]", text)
    return int(match.group()) if match else None


async def run_eval(golden_yaml_path: str, conn, config: Config) -> str:
    repo_id, cases = load_golden(golden_yaml_path)

    row = conn.execute(
        "SELECT embedding_model, embedding_dim FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Repo {repo_id} not found")
    embedding_model, embedding_dim = row

    embedder = _resolve_embedder(embedding_model, config)
    chain = get_fallback_chain(config)
    judge = get_generator(config)

    results: list[CaseResult] = []
    for case in cases:
        query_vector = (await embedder.embed_batch([case.question]))[0]
        search_results = search_chunks(conn, repo_id, query_vector, dim=embedding_dim, top_k=TOP_K)
        hit, rr = _hit_and_rank(search_results, case)

        prompt = build_prompt(case.question, search_results)
        answer = "".join(
            [t async for t in chat_with_fallback(chain, [{"role": "user", "content": prompt}], stream=False)]
        )

        judge_score = None
        if case.reference_answer:
            judge_text = "".join(
                [
                    t
                    async for t in judge.chat(
                        [
                            {
                                "role": "user",
                                "content": JUDGE_PROMPT.format(
                                    reference=case.reference_answer, produced=answer
                                ),
                            }
                        ],
                        stream=False,
                    )
                ]
            )
            judge_score = _parse_judge_score(judge_text)

        results.append(CaseResult(case.question, hit, rr, judge_score, answer))

    return _format_table(results)


def _format_table(results: list[CaseResult]) -> str:
    if not results:
        return "No cases to evaluate."

    hit_rate = sum(1 for r in results if r.hit) / len(results)
    mrr = sum(r.reciprocal_rank for r in results) / len(results)
    scored = [r.judge_score for r in results if r.judge_score is not None]
    avg_judge = sum(scored) / len(scored) if scored else None

    lines = [f"{'question':50s}  {'hit':5s}  {'rr':5s}  {'judge':5s}"]
    for r in results:
        judge_str = str(r.judge_score) if r.judge_score is not None else "-"
        lines.append(f"{r.question[:50]:50s}  {str(r.hit):5s}  {r.reciprocal_rank:.2f}  {judge_str:5s}")
    lines.append("")
    avg_judge_str = avg_judge if avg_judge is not None else "n/a"
    lines.append(f"hit-rate@{TOP_K}: {hit_rate:.2f}   MRR: {mrr:.2f}   avg judge: {avg_judge_str}")
    return "\n".join(lines)
```

`_resolve_embedder` here duplicates the one in `sleuth/retrieve/answer.py` (same reason `_chunks_table` is duplicated between `store.py`/`search.py`, Task 8/10) — small, self-contained, keeps modules independent. Not worth a shared-utils module for four lines.

- [ ] **Step 7: Run test to verify it passes**

Run: `docker compose up -d && sleep 2 && pytest tests/test_eval_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add sleuth/eval/__init__.py sleuth/eval/runner.py eval/sample_repo.yaml tests/fixtures/sample_golden.yaml tests/test_eval_runner.py
git commit -m "feat: add sleuth eval harness (hit-rate/MRR/LLM-judge)"
```

---

## Manual End-to-End Check (after Task 14)

With real API keys in `.env` and `docker compose up -d` running:

```bash
# Indexed mode
python -m sleuth add https://github.com/psf/requests
python -m sleuth list
python -m sleuth ask <repo_id> "How does the Session class handle cookies?"

# Live/agentic mode -- no indexing wait, runs against a local directory
python -m sleuth agentic . "What does the ingest pipeline do?"

# Eval harness -- point eval/sample_repo.yaml's `repo` field at the repo_id
# printed by `sleuth add` above, then:
python -m sleuth eval eval/sample_repo.yaml
```

Confirms the whole pipeline works against a real repo, real Voyage/NIM embeddings, and real Groq/NIM calls — not just mocks — for both retrieval modes plus the eval harness.

