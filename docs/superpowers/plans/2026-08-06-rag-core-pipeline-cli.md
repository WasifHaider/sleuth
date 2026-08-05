# RAG Core Pipeline + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core ingest→embed→store→retrieve→answer pipeline for the RAG code chatbot, exposed through a CLI, so a user can point it at a GitHub repo and ask questions about it — no FastAPI/React yet (that's Plan 2).

**Architecture:** A Python package `sleuth/` with two pipelines sharing a Postgres (pgvector) store: `ingest` (clone → tree-sitter parse → chunk → hash-diff → embed → upsert) and `retrieve` (embed query → vector search → prompt → Groq). Every module is plain functions/classes with no framework dependency, so the CLI (this plan) and the future FastAPI layer (Plan 2) call the exact same code.

**Tech Stack:** Python 3.11+, tree-sitter (+ tree-sitter-python/javascript/typescript grammars), httpx (direct REST calls to Voyage and Groq — no vendor SDKs, so every request/response is visible), psycopg 3 + pgvector (direct SQL against Postgres, no ORM), pytest/pytest-asyncio/respx for tests, Docker (local Postgres+pgvector for dev/tests, schema is identical to Supabase since Supabase is just Postgres).

## Global Constraints

- Language: Python for all backend/pipeline code (per spec).
- Chunking granularity: function/method/top-level-class via tree-sitter AST walk, plus one module-level fallback chunk per file for top-level code outside any function/class (per spec, "Chunking Rationale").
- Initial language support: Python, JavaScript, TypeScript only (per spec Non-Goals — other languages are additive later).
- Embeddings: Voyage AI, model `voyage-code-3`, `output_dimension=1024` (per spec).
- Vector store: Postgres + pgvector, schema exactly as defined in spec's Data Model section (`repos`, `chunks` tables), reachable via a plain connection string (Supabase in prod, local Docker Postgres in dev/tests — same schema, no code branches for environment).
- Generation: Groq API, default model `llama-3.3-70b-versatile`, model name must be a single config value (swappable without code changes).
- Re-indexing must be incremental: skip re-embedding chunks whose `content_hash` is unchanged since the last index (per spec addendum).
- Embedding calls must be sent with bounded concurrency (async), not strictly sequential (per spec addendum).
- Error handling: a single file that fails to parse must not abort the whole repo index (skip + continue); a repo that fails to clone must land in `repos.status = 'failed'` with `error_message` set, not raise past the pipeline boundary uncaught; a chat query against a repo whose status isn't `'ready'` must be rejected with a clear error before any LLM call.
- No vendor SDKs for Voyage/Groq — call their HTTP APIs directly via httpx so every request is inspectable code, not library internals.

---

## File Structure

```
sleuth/
  __init__.py
  __main__.py            # `python -m sleuth` entry point
  config.py               # env-var driven Config
  db.py                    # connection + schema application
  store.py                 # repo/chunk CRUD (raw SQL)
  chunking.py              # Chunk dataclass + context formatting (shared by embed + answer)
  http_retry.py             # shared retry-with-backoff wrapper for Voyage/Groq calls
  cli.py                    # argparse CLI: add / list / ask
  ingest/
    __init__.py
    clone.py                # shallow git clone + file listing
    parse.py                 # tree-sitter language registry + parse_source()
    chunk.py                 # chunk_source(): walks AST -> list[Chunk]
    embed.py                  # Voyage REST calls, batched + concurrent
    pipeline.py               # orchestrates ingest_repo()
  retrieve/
    __init__.py
    search.py                 # pgvector similarity search
    answer.py                  # prompt building + Groq REST call
schema.sql                    # repos + chunks tables, pgvector extension, indexes
docker-compose.yml             # local Postgres+pgvector for dev/tests
requirements.txt
.env.example
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
  test_cli.py
```

Design decision worth calling out: a class is only ever chunked as a whole (`kind='class'`) if it has **no** method-like children. If it has methods, each method becomes its own chunk (`symbol_name = "ClassName.method_name"`) and the class itself is not separately chunked — avoids storing the same code twice (once inside the class chunk, once inside each method chunk).

---

### Task 1: Project scaffolding and config

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `sleuth/__init__.py`
- Create: `sleuth/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `sleuth.config.Config` dataclass with fields `voyage_api_key: str`, `groq_api_key: str`, `groq_model: str`, `database_url: str`; `sleuth.config.load_config() -> Config`; `sleuth.config.ConfigError(Exception)`.

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
pytest>=8,<9
pytest-asyncio>=0.24,<0.25
respx>=0.21,<0.22
```

- [ ] **Step 2: Write .env.example**

```
VOYAGE_API_KEY=
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

    assert config.voyage_api_key == "voyage-key"
    assert config.groq_api_key == "groq-key"
    assert config.groq_model == "llama-3.3-70b-versatile"
    assert config.database_url == "postgresql://u:p@localhost:5432/db"


def test_load_config_defaults_groq_model(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.groq_model == "llama-3.3-70b-versatile"


def test_load_config_raises_on_missing_required_var(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        load_config()
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
REQUIRED_VARS = ("VOYAGE_API_KEY", "GROQ_API_KEY", "DATABASE_URL")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    voyage_api_key: str
    groq_api_key: str
    groq_model: str
    database_url: str


def load_config() -> Config:
    missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    return Config(
        voyage_api_key=os.environ["VOYAGE_API_KEY"],
        groq_api_key=os.environ["GROQ_API_KEY"],
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        database_url=os.environ["DATABASE_URL"],
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example sleuth/__init__.py sleuth/config.py tests/test_config.py
git commit -m "feat: add project scaffolding and config loading"
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

CREATE TABLE IF NOT EXISTS repos (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_url    text NOT NULL,
    status        text NOT NULL DEFAULT 'pending',
    error_message text,
    indexed_at    timestamptz
);

CREATE TABLE IF NOT EXISTS chunks (
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

CREATE UNIQUE INDEX IF NOT EXISTS chunks_identity_idx
    ON chunks (repo_id, file_path, COALESCE(symbol_name, ''));

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE EXTENSION IF NOT EXISTS pgcrypto;
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
    assert "chunks" in table_names
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
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add schema.sql docker-compose.yml sleuth/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: add Postgres schema and connection helper"
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

### Task 7: Shared HTTP retry helper + Voyage embedding client (batched, concurrent)

**Files:**
- Create: `sleuth/http_retry.py`
- Create: `sleuth/ingest/embed.py`
- Test: `tests/test_http_retry.py`
- Test: `tests/test_embed.py`

**Interfaces:**
- Produces: `async def sleuth.http_retry.post_with_retry(client: httpx.AsyncClient, url: str, *, retries: int = 1, backoff_seconds: float = 1.0, **kwargs) -> httpx.Response` (retries once on a transient failure — network error or 429/500/502/503/504 — then raises on the response via `raise_for_status()`; a non-transient error status raises immediately with no retry).
- Produces: `async def sleuth.ingest.embed.embed_texts(texts: list[str], api_key: str, model: str = "voyage-code-3", output_dimension: int = 1024, batch_size: int = 128, max_concurrency: int = 5) -> list[list[float]]` — return order matches input order. Uses `post_with_retry` internally (per spec's error-handling requirement: transient Voyage/Groq failures get one retry with backoff before surfacing).

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

- [ ] **Step 6: Write the failing test for the embedding client**

```python
# tests/test_embed.py
import httpx
import pytest
import respx

from sleuth.ingest.embed import embed_texts


@pytest.mark.asyncio
@respx.mock
async def test_embed_texts_batches_and_preserves_order():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = httpx.Request.read(request) if False else request.content
        import json

        body = json.loads(payload)
        inputs = body["input"]
        assert body["model"] == "voyage-code-3"
        assert body["output_dimension"] == 1024
        # embedding = [len(text), 0.0, ...] so we can assert order downstream
        data = [{"embedding": [float(len(text)), 0.0]} for text in inputs]
        return httpx.Response(200, json={"data": data})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    result = await embed_texts(texts, api_key="key", batch_size=2, max_concurrency=5)

    assert [vec[0] for vec in result] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert call_count == 3  # batches of 2, 2, 1


@pytest.mark.asyncio
@respx.mock
async def test_embed_texts_sends_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    await embed_texts(["only one"], api_key="secret-key")
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.ingest.embed'`

- [ ] **Step 8: Write sleuth/ingest/embed.py**

```python
import asyncio

import httpx

from sleuth.http_retry import post_with_retry

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _embed_batch(
    client: httpx.AsyncClient, batch: list[str], api_key: str, model: str, output_dimension: int, sem: asyncio.Semaphore
) -> list[list[float]]:
    async with sem:
        response = await post_with_retry(
            client,
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": batch, "model": model, "output_dimension": output_dimension},
        )
        data = response.json()["data"]
        return [item["embedding"] for item in data]


async def embed_texts(
    texts: list[str],
    api_key: str,
    model: str = "voyage-code-3",
    output_dimension: int = 1024,
    batch_size: int = 128,
    max_concurrency: int = 5,
) -> list[list[float]]:
    if not texts:
        return []

    batches = _batches(texts, batch_size)
    sem = asyncio.Semaphore(max_concurrency)

    async with httpx.AsyncClient(timeout=60) as client:
        results = await asyncio.gather(
            *[_embed_batch(client, batch, api_key, model, output_dimension, sem) for batch in batches]
        )

    embeddings: list[list[float]] = []
    for batch_result in results:
        embeddings.extend(batch_result)
    return embeddings
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_embed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Commit**

```bash
git add sleuth/ingest/embed.py tests/test_embed.py
git commit -m "feat: add concurrent batched Voyage embedding client"
```

---

### Task 8: Repo/chunk store (raw SQL CRUD)

**Files:**
- Create: `sleuth/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `sleuth.chunking.Chunk`, `sleuth.db.get_connection`/`apply_schema` (via `pg_conn` fixture).
- Produces: `sleuth.store.create_repo(conn, github_url: str) -> str` (repo id), `sleuth.store.update_repo_status(conn, repo_id: str, status: str, error_message: str | None = None) -> None`, `sleuth.store.get_existing_hashes(conn, repo_id: str) -> dict[tuple[str, str | None], str]`, `sleuth.store.upsert_chunks(conn, repo_id: str, chunks_with_embeddings: list[tuple[Chunk, list[float]]]) -> None`, `sleuth.store.delete_stale_chunks(conn, repo_id: str, current_keys: set[tuple[str, str | None]]) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from sleuth.chunking import Chunk
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
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


def test_upsert_and_get_existing_hashes(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    chunk_b = Chunk("f.py", None, "module", 3, 3, "X = 1\n")

    upsert_chunks(
        pg_conn,
        repo_id,
        [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)],
    )
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id)

    assert hashes[("f.py", "foo")] == chunk_a.content_hash
    assert hashes[("f.py", None)] == chunk_b.content_hash


def test_upsert_overwrites_existing_row_on_conflict(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    original = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n")
    upsert_chunks(pg_conn, repo_id, [(original, [0.1] * 1024)])
    pg_conn.commit()

    changed = Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 2\n")
    upsert_chunks(pg_conn, repo_id, [(changed, [0.9] * 1024)])
    pg_conn.commit()

    count = pg_conn.execute("SELECT count(*) FROM chunks WHERE repo_id = %s", (repo_id,)).fetchone()[0]
    assert count == 1

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert hashes[("f.py", "foo")] == changed.content_hash


def test_delete_stale_chunks_removes_missing_keys(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()

    chunk_a = Chunk("f.py", "foo", "function", 1, 2, "code a")
    chunk_b = Chunk("g.py", "bar", "function", 1, 2, "code b")
    upsert_chunks(pg_conn, repo_id, [(chunk_a, [0.1] * 1024), (chunk_b, [0.2] * 1024)])
    pg_conn.commit()

    delete_stale_chunks(pg_conn, repo_id, current_keys={("f.py", "foo")})
    pg_conn.commit()

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert set(hashes) == {("f.py", "foo")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.store'`

- [ ] **Step 3: Write sleuth/store.py**

```python
from sleuth.chunking import Chunk


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


def get_existing_hashes(conn, repo_id: str) -> dict[tuple[str, str | None], str]:
    rows = conn.execute(
        "SELECT file_path, symbol_name, content_hash FROM chunks WHERE repo_id = %s",
        (repo_id,),
    ).fetchall()
    return {(file_path, symbol_name): content_hash for file_path, symbol_name, content_hash in rows}


def upsert_chunks(conn, repo_id: str, chunks_with_embeddings: list[tuple[Chunk, list[float]]]) -> None:
    for chunk, embedding in chunks_with_embeddings:
        conn.execute(
            """
            INSERT INTO chunks
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


def delete_stale_chunks(conn, repo_id: str, current_keys: set[tuple[str, str | None]]) -> None:
    existing = get_existing_hashes(conn, repo_id)
    stale = [key for key in existing if key not in current_keys]
    for file_path, symbol_name in stale:
        conn.execute(
            "DELETE FROM chunks WHERE repo_id = %s AND file_path = %s AND symbol_name IS NOT DISTINCT FROM %s",
            (repo_id, file_path, symbol_name),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/store.py tests/test_store.py
git commit -m "feat: add repo/chunk store with incremental upsert and stale cleanup"
```

---

### Task 9: Ingest pipeline orchestration

**Files:**
- Create: `sleuth/ingest/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `clone_repo`, `list_source_files` (Task 6); `chunk_source` (Task 5); `embed_texts` (Task 7); `format_chunk_context` (Task 3); `create_repo`, `update_repo_status`, `get_existing_hashes`, `upsert_chunks`, `delete_stale_chunks` (Task 8); `Config` (Task 1).
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
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)

    row = pg_conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    assert row[0] == "ready"

    hashes = get_existing_hashes(pg_conn, repo_id)
    assert ("a.py", "foo") in hashes
    assert ("b.py", "bar") in hashes


@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_skips_unchanged_chunks_on_reindex(pg_conn, local_git_repo):
    _mock_voyage()
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")

    repo_id = await ingest_repo(str(local_git_repo), pg_conn, config)
    hashes_before = get_existing_hashes(pg_conn, repo_id)

    # change only a.py
    (local_git_repo / "a.py").write_text("def foo():\n    return 999\n")
    subprocess.run(["git", "add", "."], cwd=local_git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "change a"], cwd=local_git_repo, check=True, capture_output=True)

    call_count_before = len(respx.calls)
    repo_id_2 = await ingest_repo(str(local_git_repo), pg_conn, config)
    calls_during_reindex = len(respx.calls) - call_count_before

    hashes_after = get_existing_hashes(pg_conn, repo_id_2)

    assert hashes_after[("a.py", "foo")] != hashes_before[("a.py", "foo")]
    assert hashes_after[("b.py", "bar")] == hashes_before[("b.py", "bar")]
    # only the changed chunk (and possibly a module-level chunk if present) got re-embedded
    assert calls_during_reindex >= 1


@pytest.mark.asyncio
async def test_ingest_repo_marks_failed_on_clone_error(pg_conn, tmp_path):
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")

    repo_id = await ingest_repo(str(tmp_path / "nope"), pg_conn, config)

    row = pg_conn.execute(
        "SELECT status, error_message FROM repos WHERE id = %s", (repo_id,)
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] is not None
```

Note: this test reindexes into the *same* pipeline call which creates a *new* `repos` row each call (per the spec's "re-pointing at a repo creates a new repos row" data model) — the incremental skip is scoped per-repo-id via `get_existing_hashes(conn, repo_id)`, so a brand-new repo_id would normally have no existing hashes. To actually test the skip behavior, `ingest_repo` must reuse an existing repo_id when re-pointed at the *same* URL. Adjust the implementation (Step 3 below) so `ingest_repo` looks up an existing `repos` row by `github_url` first and reuses its id if found, only creating a new row for a URL never seen before — this is also better UX (re-adding the same repo updates it in place rather than creating duplicates).

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
from sleuth.ingest.embed import embed_texts
from sleuth.ingest.parse import LANGUAGES
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
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
        existing_hashes = get_existing_hashes(conn, repo_id)

        to_embed = [c for c in all_chunks if existing_hashes.get((c.file_path, c.symbol_name)) != c.content_hash]

        if to_embed:
            texts = [
                format_chunk_context(c, EXTENSION_TO_LANGUAGE.get("." + c.file_path.rsplit(".", 1)[-1], ""))
                for c in to_embed
            ]
            vectors = await embed_texts(texts, api_key=config.voyage_api_key)
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)))
            conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys)
        conn.commit()

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose up -d && sleep 2 && pytest tests/test_pipeline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: add ingest pipeline orchestration with incremental re-index"
```

---

### Task 10: Vector search

**Files:**
- Create: `sleuth/retrieve/__init__.py`
- Create: `sleuth/retrieve/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `Chunk` (Task 3), `pg_conn` fixture.
- Produces: `sleuth.retrieve.search.SearchResult` dataclass (`file_path`, `symbol_name`, `kind`, `start_line`, `end_line`, `code_text`, `distance: float`), `sleuth.retrieve.search.search_chunks(conn, repo_id: str, query_embedding: list[float], top_k: int = 8) -> list[SearchResult]`.

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
    )
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_id, _one_hot(1), top_k=2)

    assert len(results) == 2
    assert results[0].symbol_name == "bar"
    assert results[0].distance < results[1].distance


def test_search_chunks_scoped_to_repo_id(pg_conn):
    repo_a = create_repo(pg_conn, "https://github.com/example/repo-a")
    repo_b = create_repo(pg_conn, "https://github.com/example/repo-b")
    pg_conn.commit()

    upsert_chunks(pg_conn, repo_a, [(Chunk("f.py", "foo", "function", 1, 2, "code"), _one_hot(0))])
    upsert_chunks(pg_conn, repo_b, [(Chunk("g.py", "bar", "function", 1, 2, "code"), _one_hot(0))])
    pg_conn.commit()

    results = search_chunks(pg_conn, repo_a, _one_hot(0), top_k=10)

    assert len(results) == 1
    assert results[0].symbol_name == "foo"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.retrieve.search'`

- [ ] **Step 4: Write sleuth/retrieve/search.py**

```python
from dataclasses import dataclass


@dataclass
class SearchResult:
    file_path: str
    symbol_name: str | None
    kind: str
    start_line: int
    end_line: int
    code_text: str
    distance: float


def search_chunks(conn, repo_id: str, query_embedding: list[float], top_k: int = 8) -> list[SearchResult]:
    rows = conn.execute(
        """
        SELECT file_path, symbol_name, kind, start_line, end_line, code_text,
               embedding <=> %s AS distance
        FROM chunks
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
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add sleuth/retrieve/__init__.py sleuth/retrieve/search.py tests/test_search.py
git commit -m "feat: add pgvector similarity search"
```

---

### Task 11: Answer generation (prompt + Groq call)

**Files:**
- Create: `sleuth/retrieve/answer.py`
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: `SearchResult` (Task 10), `search_chunks` (Task 10), `embed_texts` (Task 7), `post_with_retry` (Task 7), `Config` (Task 1).
- Produces: `sleuth.retrieve.answer.build_prompt(question: str, results: list[SearchResult]) -> str`, `async def sleuth.retrieve.answer.get_answer(question: str, repo_id: str, conn, config: Config) -> str` (raises `ValueError` if `repos.status != 'ready'`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_answer.py
import json

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.retrieve.answer import build_prompt, get_answer
from sleuth.retrieve.search import SearchResult
from sleuth.chunking import Chunk
from sleuth.store import create_repo, update_repo_status, upsert_chunks


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
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")

    with pytest.raises(ValueError, match="not ready"):
        await get_answer("question?", repo_id, pg_conn, config)

    assert len(respx.calls) == 0


@pytest.mark.asyncio
@respx.mock
async def test_get_answer_calls_groq_and_returns_content(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn,
        repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    def voyage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})

    def groq_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert any("foo" in m["content"] for m in body["messages"])
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "foo returns 1."}}]}
        )

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=voyage_handler)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=groq_handler)

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="test-model", database_url="unused")
    answer = await get_answer("What does foo do?", repo_id, pg_conn, config)

    assert answer == "foo returns 1."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_answer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sleuth.retrieve.answer'`

- [ ] **Step 3: Write sleuth/retrieve/answer.py**

```python
import httpx

from sleuth.config import Config
from sleuth.http_retry import post_with_retry
from sleuth.ingest.embed import embed_texts
from sleuth.retrieve.search import SearchResult, search_chunks

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a code assistant. Answer the user's question about the repository "
    "using only the provided code excerpts. If the excerpts don't contain the "
    "answer, say so explicitly rather than guessing."
)


def build_prompt(question: str, results: list[SearchResult]) -> str:
    blocks = []
    for r in results:
        symbol = r.symbol_name or "(module level)"
        blocks.append(
            f"# File: {r.file_path}\n# {r.kind}: {symbol} (lines {r.start_line}-{r.end_line})\n\n{r.code_text}"
        )
    context = "\n\n---\n\n".join(blocks)
    return f"Question: {question}\n\nRelevant code:\n\n{context}"


async def get_answer(question: str, repo_id: str, conn, config: Config) -> str:
    row = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None or row[0] != "ready":
        raise ValueError(f"Repo {repo_id} is not ready to query (status={row[0] if row else 'missing'})")

    query_vector = (await embed_texts([question], api_key=config.voyage_api_key))[0]
    results = search_chunks(conn, repo_id, query_vector)
    prompt = build_prompt(question, results)

    async with httpx.AsyncClient(timeout=60) as client:
        response = await post_with_retry(
            client,
            GROQ_URL,
            headers={"Authorization": f"Bearer {config.groq_api_key}"},
            json={
                "model": config.groq_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        return response.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_answer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sleuth/retrieve/answer.py tests/test_answer.py
git commit -m "feat: add prompt building and Groq answer generation"
```

---

### Task 12: CLI

**Files:**
- Create: `sleuth/cli.py`
- Create: `sleuth/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ingest_repo` (Task 9), `get_answer` (Task 11), `get_connection`/`apply_schema` (Task 2), `load_config` (Task 1).
- Produces: `sleuth.cli.main(argv: list[str] | None = None) -> None`.

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
        return httpx.Response(200, json={"choices": [{"message": {"content": "foo returns 1."}}]})

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
from sleuth.ingest.pipeline import ingest_repo
from sleuth.retrieve.answer import get_answer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sleuth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Clone and index a repo")
    add_parser.add_argument("github_url")

    subparsers.add_parser("list", help="List indexed repos")

    ask_parser = subparsers.add_parser("ask", help="Ask a question about an indexed repo")
    ask_parser.add_argument("repo_id")
    ask_parser.add_argument("question")

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
        answer = await get_answer(args.question, args.repo_id, conn, config)
        print(answer)

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

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose up -d && sleep 2 && pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add sleuth/cli.py sleuth/__main__.py tests/test_cli.py
git commit -m "feat: add CLI (add/list/ask) wiring pipeline and retrieval together"
```

---

## Manual End-to-End Check (after Task 12)

With real API keys in `.env` and `docker compose up -d` running:

```bash
python -m sleuth add https://github.com/psf/requests
python -m sleuth list
python -m sleuth ask <repo_id> "How does the Session class handle cookies?"
```

Confirms the whole pipeline works against a real repo, real Voyage embeddings, and a real Groq call — not just mocks.
