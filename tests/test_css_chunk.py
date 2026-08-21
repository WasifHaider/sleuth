from sleuth.ingest.chunk import chunk_source

CSS_SOURCE = b""".foo {
  color: red;
}

.bar, .baz {
  color: blue;
}

@media screen and (min-width: 100px) {
  .responsive { color: green; }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@font-face {
  font-family: "MyFont";
}
"""


def test_chunk_source_css_produces_one_chunk_per_rule():
    chunks = chunk_source(CSS_SOURCE, "styles/app.css", ".css")

    assert all(c.kind in ("rule", "module") for c in chunks)
    rule_chunks = [c for c in chunks if c.kind == "rule"]

    symbol_names = {c.symbol_name for c in rule_chunks}
    assert ".foo" in symbol_names
    assert ".bar, .baz" in symbol_names
    assert any(name.startswith("@media") for name in symbol_names)
    assert any(name.startswith("@keyframes spin") for name in symbol_names)
    assert any(name.startswith("@font-face") for name in symbol_names)


def test_chunk_source_css_rule_chunk_contains_full_declaration_block():
    chunks = chunk_source(CSS_SOURCE, "styles/app.css", ".css")
    by_symbol = {c.symbol_name: c for c in chunks if c.kind == "rule"}

    assert "color: red" in by_symbol[".foo"].code_text
    assert by_symbol[".foo"].start_line == 1
    assert by_symbol[".foo"].end_line == 3


def test_chunk_source_css_media_block_kept_as_single_chunk_not_split():
    chunks = chunk_source(CSS_SOURCE, "styles/app.css", ".css")
    media_chunks = [c for c in chunks if c.symbol_name and c.symbol_name.startswith("@media")]

    assert len(media_chunks) == 1
    assert ".responsive" in media_chunks[0].code_text


def test_chunk_source_css_no_error_on_comment_and_import_leftover():
    source = b'/* header */\n@import url("x.css");\n\n.foo { color: red; }\n'
    chunks = chunk_source(source, "styles/app.css", ".css")

    by_kind = {c.kind: c for c in chunks}
    assert "rule" in by_kind
    assert by_kind["rule"].symbol_name == ".foo"
    # comment + @import aren't rule_set/media/keyframes/at_rule, so they land
    # in the leftover module chunk rather than being silently dropped
    assert "module" in by_kind
    assert "@import" in by_kind["module"].code_text
