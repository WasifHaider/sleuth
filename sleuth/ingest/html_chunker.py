from tree_sitter import Node

from sleuth.chunking import Chunk
from sleuth.ingest.markup_shared import attribute_value, chunk_embedded_block, node_text

# <script type="...">'s value decides whether the block is actually
# JavaScript at all — unlike Vue's <script lang="ts">, plain HTML overloads
# <script> for non-code payloads too (JSON-LD, import maps, Handlebars/Mustache
# templates via text/template, etc.). Chunking those as "JavaScript" would be
# actively misleading (wrong kind, wrong retrieval signal), so only a script
# with NO type attribute or a recognized JS mime type gets routed through the
# JS chunker; anything else is skipped outright — same honest-degradation
# instinct as .scss and unrecognized Vue <style lang="...">, applied here to
# script TYPE instead of file extension.
_JS_SCRIPT_TYPES = {
    "text/javascript",
    "application/javascript",
    "module",
    "text/babel",  # common in quick prototypes using in-browser Babel
}
_SCRIPT_DEFAULT_EXTENSION = ".js"
_STYLE_DEFAULT_EXTENSION = ".css"
# HTML's <script>/<style> have no lang="ts"-style attribute (that's a Vue-only
# convention) — content type is signalled via type="text/javascript" etc.
# instead. chunk_embedded_block's lang_attribute parameter is repurposed to
# read "type" here; the (currently single-entry) lang_map keeps its shape
# compatible with the shared helper without adding a second code path.
_SCRIPT_LANG_MAP = {t: ".js" for t in _JS_SCRIPT_TYPES}


def _is_chunkable_script(start_tag: Node, source_bytes: bytes) -> bool:
    script_type = attribute_value(start_tag, "type", source_bytes)
    return script_type is None or script_type.lower() in _JS_SCRIPT_TYPES


def _element_symbol_name(element: Node, source_bytes: bytes) -> str:
    start_tag = element.children[0] if element.children else element
    text = node_text(start_tag, source_bytes)
    return text if len(text) <= 80 else text[:77] + "..."


def _tag_name(element: Node, source_bytes: bytes) -> str | None:
    if not element.children:
        return None
    start_tag = element.children[0]
    name_node = next((c for c in start_tag.children if c.type == "tag_name"), None)
    return node_text(name_node, source_bytes).lower() if name_node is not None else None


def _sectioning_roots(root: Node, source_bytes: bytes) -> list[Node]:
    # A full HTML document wraps EVERYTHING in a single <html> element, which
    # itself wraps <head> and <body> — chunking at "root.children" directly
    # would produce exactly one giant chunk covering the whole page (confirmed
    # by direct test against a real document skeleton before writing this).
    # <head> and <body> are the actual meaningful sectioning boundaries for a
    # full document, so this descends through the html>head/body wrapper
    # specifically and returns THEIR children as the chunk-worthy set instead.
    # A fragment file with no <html> wrapper at all (common for partials/
    # includes) falls through unchanged to using root.children directly.
    html_element = next(
        (c for c in root.children if c.type == "element" and _tag_name(c, source_bytes) == "html"),
        None,
    )
    if html_element is None:
        return list(root.children)

    sections: list[Node] = []
    for child in html_element.children:
        if child.type != "element":
            continue
        tag = _tag_name(child, source_bytes)
        if tag in ("head", "body"):
            sections.extend(child.children)
        else:
            sections.append(child)  # unusual: element directly under <html>, not head/body
    return sections


def _chunk_body_elements(root: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    # Structural chunking, same instinct as CSS rules and Vue <template>
    # elements: HTML has no function/class shape, so one chunk per
    # "sectioning" element (see _sectioning_roots) — recursively including
    # everything nested under each one as that one chunk's text (mirrors how
    # a human thinks of "this section of markup" as one unit, same reasoning
    # as Vue's template chunker). <script>/<style> elements are excluded here
    # — they're chunked separately by chunk_html via _find_embedded_blocks,
    # and would otherwise show up TWICE (once as a raw "element" chunk, once
    # properly parsed as JS/CSS).
    chunks = []
    for child in _sectioning_roots(root, source_bytes):
        if child.type != "element":
            continue  # doctype / comments / stray text: no indexable content
        if _tag_name(child, source_bytes) in ("script", "style"):
            continue
        chunks.append(
            Chunk(
                file_path=file_path,
                symbol_name=_element_symbol_name(child, source_bytes),
                kind="element",
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                code_text=node_text(child, source_bytes),
            )
        )
    return chunks


def _find_embedded_blocks(node: Node, kind: str) -> list[Node]:
    # <script>/<style> tags are only findable by walking the WHOLE tree, not
    # just document-level children, because in normal HTML they're nested
    # under <html><head> (or scattered throughout <body>) — unlike Vue SFCs,
    # which put script/style at the top level by convention. A plain
    # recursive walk (not a tree-sitter query) is enough here: no dedup/
    # overlap resolution is needed since script_element/style_element nodes
    # never nest inside each other or overlap.
    found = []
    if node.type == kind:
        found.append(node)
        return found  # script/style elements have no further script/style children
    for child in node.children:
        found.extend(_find_embedded_blocks(child, kind))
    return found


def chunk_html(root: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    chunks = _chunk_body_elements(root, source_bytes, file_path)

    for script_element in _find_embedded_blocks(root, "script_element"):
        start_tag = script_element.children[0]
        if not _is_chunkable_script(start_tag, source_bytes):
            continue
        chunks.extend(
            chunk_embedded_block(
                script_element,
                source_bytes,
                file_path,
                _SCRIPT_LANG_MAP,
                _SCRIPT_DEFAULT_EXTENSION,
                lang_attribute="type",
            )
        )

    for style_element in _find_embedded_blocks(root, "style_element"):
        chunks.extend(
            chunk_embedded_block(
                style_element,
                source_bytes,
                file_path,
                {},  # HTML <style> has no meaningful "type" values beyond CSS
                _STYLE_DEFAULT_EXTENSION,
                lang_attribute="type",
            )
        )

    return chunks
