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
