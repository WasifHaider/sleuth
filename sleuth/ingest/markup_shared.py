"""Shared helpers for tree-sitter-based markup languages (HTML, Vue SFCs) that
embed OTHER languages inline via <script>/<style> tags. Both grammars produce
the same node shapes for tags/attributes (start_tag -> attribute ->
attribute_name/quoted_attribute_value, script_element/style_element ->
raw_text), so this logic is written once and reused rather than duplicated
per markup language.
"""
from tree_sitter import Node

from sleuth.chunking import Chunk


def node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def attribute_value(start_tag: Node, attribute_name: str, source_bytes: bytes) -> str | None:
    for child in start_tag.children:
        if child.type != "attribute":
            continue
        name_node = next((c for c in child.children if c.type == "attribute_name"), None)
        if name_node is None or node_text(name_node, source_bytes) != attribute_name:
            continue
        for value_holder in child.children:
            if value_holder.type == "quoted_attribute_value":
                value_node = next(
                    (c for c in value_holder.children if c.type == "attribute_value"), None
                )
                if value_node is not None:
                    return node_text(value_node, source_bytes)
            elif value_holder.type == "attribute_value":
                return node_text(value_holder, source_bytes)
        return None  # attribute present but no value (e.g. bare `defer`, bare `setup`)
    return None


def resolve_extension(lang: str | None, lang_map: dict[str, str], default_ext: str) -> str:
    if lang is None:
        return default_ext
    return lang_map.get(lang.lower(), f".{lang.lower()}")


def raw_text_node(element: Node) -> Node | None:
    return next((c for c in element.children if c.type == "raw_text"), None)


def chunk_embedded_block(
    element: Node,
    source_bytes: bytes,
    file_path: str,
    lang_map: dict[str, str],
    default_ext: str,
    lang_attribute: str,
) -> list[Chunk]:
    # Local import, not module-level: this module is imported by chunk.py
    # (indirectly, via vue_chunker.py / html_chunker.py), so importing
    # chunk_source from chunk.py at module load time here would be a
    # circular import. By call time (this is only ever invoked from inside
    # chunk_source's own dispatch) chunk.py is already fully loaded, so the
    # deferred import here succeeds fine.
    from sleuth.ingest.chunk import chunk_source

    start_tag = element.children[0]
    text_node = raw_text_node(element)
    if text_node is None:
        return []  # empty <script></script> or <style></style> — nothing to chunk

    lang = attribute_value(start_tag, lang_attribute, source_bytes)
    extension = resolve_extension(lang, lang_map, default_ext)
    block_bytes = source_bytes[text_node.start_byte : text_node.end_byte]

    # text_node's own start row IS the offset needed to convert the
    # sub-chunker's line numbers (1-indexed, relative to block_bytes) back
    # into real line numbers in the original file: raw_text always starts on
    # the SAME row as the block's opening tag (immediately after its ">"),
    # so its 0-indexed start row equals exactly how many original-file rows
    # to add back on.
    line_offset = text_node.start_point[0]

    sub_chunks = chunk_source(block_bytes, file_path, extension)
    return [
        Chunk(
            file_path=c.file_path,
            symbol_name=c.symbol_name,
            kind=c.kind,
            start_line=c.start_line + line_offset,
            end_line=c.end_line + line_offset,
            code_text=c.code_text,
        )
        for c in sub_chunks
    ]
