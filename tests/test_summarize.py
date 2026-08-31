import pytest

from sleuth.chunking import Chunk
from sleuth.summarize import build_repo_map, summarize_repo


def _chunk(file_path, symbol_name, kind="function"):
    return Chunk(
        file_path=file_path, symbol_name=symbol_name, kind=kind,
        start_line=1, end_line=2, code_text="pass",
    )


def test_build_repo_map_lists_every_file_and_symbol_once():
    chunks = [
        _chunk("sleuth/store.py", "create_repo"),
        _chunk("sleuth/store.py", "get_repo"),
        _chunk("sleuth/cli.py", None, kind="module"),
    ]

    repo_map = build_repo_map(chunks)

    assert "sleuth/store.py" in repo_map
    assert "create_repo" in repo_map
    assert "get_repo" in repo_map
    assert "sleuth/cli.py" in repo_map


def test_build_repo_map_excludes_doc_chunks():
    chunks = [
        _chunk("sleuth/store.py", "create_repo"),
        _chunk("docs/architecture.html", None, kind="module"),
    ]

    repo_map = build_repo_map(chunks)

    assert "sleuth/store.py" in repo_map
    assert "docs/architecture.html" not in repo_map


class _FakeGenerator:
    def __init__(self, response):
        self.response = response
        self.received_messages = None

    async def chat(self, messages, stream=True):
        self.received_messages = messages
        yield self.response


@pytest.mark.asyncio
async def test_summarize_repo_calls_generator_with_repo_map_and_returns_text():
    chunks = [_chunk("sleuth/store.py", "create_repo")]
    generator = _FakeGenerator("This is a RAG chatbot backend.")

    summary = await summarize_repo(chunks, generator)

    assert summary == "This is a RAG chatbot backend."
    assert "sleuth/store.py" in generator.received_messages[-1]["content"]


@pytest.mark.asyncio
async def test_summarize_repo_returns_none_for_empty_chunk_list():
    generator = _FakeGenerator("unused")

    summary = await summarize_repo([], generator)

    assert summary is None
    assert generator.received_messages is None  # never called
