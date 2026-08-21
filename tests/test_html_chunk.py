from sleuth.ingest.chunk import chunk_source

FULL_DOC = b"""<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
</head>
<body>
  <header class="nav">
    <h1>Hello</h1>
  </header>
  <main>
    <p>World</p>
  </main>
  <script>
    function greet() {
      return "hi";
    }
  </script>
  <style>
    .nav { color: red; }
  </style>
</body>
</html>
"""


def test_chunk_source_html_full_document_chunks_head_and_body_sections_separately():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    element_chunks = {c.symbol_name: c for c in chunks if c.kind == "element"}

    # descends through <html><head>/<body> rather than producing one giant
    # chunk for the whole page (confirmed this was the naive/wrong behavior
    # before _sectioning_roots was added)
    assert "<title>" in element_chunks
    assert any(name.startswith("<header") for name in element_chunks)
    assert "<main>" in element_chunks


def test_chunk_source_html_full_document_produces_no_single_giant_html_chunk():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    # nothing should span the whole <html>...</html> — that would defeat the
    # entire purpose of chunking (one useless giant blob instead of sections)
    assert not any(c.symbol_name and c.symbol_name.startswith("<html") for c in chunks)


def test_chunk_source_html_script_block_routed_through_js_chunker():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["greet"].kind == "function"
    assert "function greet" in by_symbol["greet"].code_text


def test_chunk_source_html_script_line_numbers_map_back_to_original_file():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    by_symbol = {c.symbol_name: c for c in chunks}

    # "function greet() {" is line 14 of FULL_DOC (1-indexed) — verified by
    # counting FULL_DOC's actual lines, not assumed
    assert by_symbol["greet"].start_line == 14


def test_chunk_source_html_style_block_routed_through_css_chunker():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    rule_chunks = {c.symbol_name: c for c in chunks if c.kind == "rule"}

    assert ".nav" in rule_chunks
    assert "color: red" in rule_chunks[".nav"].code_text


def test_chunk_source_html_script_and_style_not_duplicated_as_raw_elements():
    chunks = chunk_source(FULL_DOC, "index.html", ".html")
    # <script>/<style> must be excluded from the plain "element" chunking
    # pass — they're handled separately via chunk_embedded_block, and would
    # otherwise show up twice (once as raw markup, once properly parsed)
    element_symbols = [c.symbol_name for c in chunks if c.kind == "element"]
    assert not any(s and s.startswith("<script") for s in element_symbols)
    assert not any(s and s.startswith("<style") for s in element_symbols)


FRAGMENT = b"""<div class="card"><h2>Title</h2></div>
<div class="footer"><p>Footer</p></div>
<script type="application/json">{"a": 1}</script>
<style>.a { color: red; }</style>
"""


def test_chunk_source_html_fragment_no_html_wrapper_chunks_top_level_elements():
    chunks = chunk_source(FRAGMENT, "partial.html", ".html")
    element_chunks = {c.symbol_name: c for c in chunks if c.kind == "element"}

    assert '<div class="card">' in element_chunks
    assert '<div class="footer">' in element_chunks


def test_chunk_source_html_script_with_non_js_type_is_not_chunked_as_javascript():
    chunks = chunk_source(FRAGMENT, "partial.html", ".html")
    # application/json must NOT be treated as JS — no function/module chunk
    # should contain the JSON payload
    assert not any('"a": 1' in c.code_text for c in chunks)


def test_chunk_source_html_style_without_type_attribute_still_chunked_as_css():
    chunks = chunk_source(FRAGMENT, "partial.html", ".html")
    rule_chunks = {c.symbol_name: c for c in chunks if c.kind == "rule"}

    assert ".a" in rule_chunks


def test_chunk_source_html_multiple_scattered_scripts_all_chunked():
    source = b"""<html>
<body>
  <script>function a() { return 1; }</script>
  <div><p>hi</p></div>
  <script type="text/javascript">function b() { return 2; }</script>
</body>
</html>
"""
    chunks = chunk_source(source, "multi.html", ".html")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["a"].kind == "function"
    assert by_symbol["b"].kind == "function"
    assert "<div>" in by_symbol


def test_chunk_source_html_empty_document_no_crash():
    chunks = chunk_source(b"<html><head></head><body></body></html>\n", "empty.html", ".html")
    assert chunks == []


def test_chunk_source_html_registered_in_supported_extensions():
    from sleuth.ingest.pipeline import SUPPORTED_EXTENSIONS

    assert ".html" in SUPPORTED_EXTENSIONS
