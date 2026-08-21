from tree_sitter import Node

from sleuth.chunking import Chunk
from sleuth.ingest.markup_shared import chunk_embedded_block, node_text

# lang="..." attribute value -> file extension to route through the EXISTING
# JS/TS chunker (Part 6 of the deep-dive doc) — no Vue-specific function/class
# parsing logic here at all, deliberately: a <script> block's content is just
# JS/TS, so it gets the exact same treatment a standalone .js/.ts file would.
_SCRIPT_LANG_EXTENSIONS = {"ts": ".ts", "typescript": ".ts", "js": ".js", "javascript": ".js"}
_SCRIPT_DEFAULT_EXTENSION = ".js"  # bare <script> with no lang= attribute is plain JS

# Only "css" (or no lang= at all) routes through the tree-sitter-css chunker.
# scss/less/stylus deliberately fall through to resolve_extension's fallback
# (".scss" etc.), which is NOT a registered LANGUAGES extension, so
# chunk_source's own UnsupportedFileType handling degrades it to the generic
# blank-line fallback chunker — same honest-degradation choice already made
# for standalone .scss files in parse.py, not a new special case.
_STYLE_LANG_EXTENSIONS = {"css": ".css"}
_STYLE_DEFAULT_EXTENSION = ".css"


def _template_symbol_name(element: Node, source_bytes: bytes) -> str:
    start_tag = element.children[0]
    text = node_text(start_tag, source_bytes)
    return text if len(text) <= 80 else text[:77] + "..."


def _chunk_template(template_element: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    # Template chunking is deliberately STRUCTURAL, same instinct as CSS
    # (Part 6 of the deep-dive doc, css_chunker.py) rather than trying to force
    # function-shaped chunks onto markup: one chunk per top-level element
    # actually rendered inside <template>, tagged kind="template". This does
    # NOT descend into v-if/v-for/mustache internals as separate chunks (that
    # would be a much bigger "Scope B" undertaking) — each top-level element
    # and everything nested inside it (however deep) is one chunk, exactly
    # mirroring how a human would think of "this component's markup block".
    chunks = []
    for child in template_element.children:
        if child.type != "element":
            continue  # skip the template's own start_tag/end_tag and stray text/comments
        chunks.append(
            Chunk(
                file_path=file_path,
                symbol_name=_template_symbol_name(child, source_bytes),
                kind="template",
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                code_text=node_text(child, source_bytes),
            )
        )
    return chunks


def chunk_vue(root: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for top in root.children:
        if top.type == "template_element":
            chunks.extend(_chunk_template(top, source_bytes, file_path))
        elif top.type == "script_element":
            chunks.extend(
                chunk_embedded_block(
                    top,
                    source_bytes,
                    file_path,
                    _SCRIPT_LANG_EXTENSIONS,
                    _SCRIPT_DEFAULT_EXTENSION,
                    lang_attribute="lang",
                )
            )
        elif top.type == "style_element":
            chunks.extend(
                chunk_embedded_block(
                    top,
                    source_bytes,
                    file_path,
                    _STYLE_LANG_EXTENSIONS,
                    _STYLE_DEFAULT_EXTENSION,
                    lang_attribute="lang",
                )
            )
        # top-level comments / stray whitespace between blocks carry no
        # indexable content — silently skipped, same as CSS's comment handling
    return chunks
