from sleuth.chunking import Chunk, format_chunk_context, is_doc_path


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


def test_is_doc_path_flags_docs_directory_at_any_depth():
    assert is_doc_path("docs/recruiter-authentication.html")
    assert is_doc_path("apps/backend/docs/sub-phase-d-plan.html")
    assert is_doc_path("docs/superpowers/plans/2026-08-19-rag-web-app.md")


def test_is_doc_path_handles_windows_style_separators():
    assert is_doc_path("apps\\backend\\docs\\sub-phase-d-plan.html")


def test_is_doc_path_ignores_real_source_files():
    assert not is_doc_path("sleuth/api/routes/chat.py")
    assert not is_doc_path("web/src/components/ChatScreen.jsx")
    # A filename that merely CONTAINS "docs" isn't the same as a docs/
    # directory segment — only an exact path-component match should count.
    assert not is_doc_path("src/docsite_helpers.py")


def test_chunk_is_doc_property_reflects_file_path():
    doc_chunk = Chunk("docs/architecture.html", None, "element", 1, 2, "<p>hi</p>")
    code_chunk = Chunk("sleuth/store.py", "get_repo", "function", 1, 2, "def get_repo(): ...")

    assert doc_chunk.is_doc is True
    assert code_chunk.is_doc is False
