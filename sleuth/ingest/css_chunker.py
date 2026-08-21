from tree_sitter import Node

from sleuth.chunking import Chunk

# Top-level CSS node types that make sense as their own retrievable chunk —
# each is a self-contained rule (a selector + declarations, or an at-rule
# with its own block). Nodes not in this set (comments, @import, @charset,
# stray tokens) fall through to a single leftover "module" chunk, same
# convention as the Python/JS/TS chunker's junk bucket.
_RULE_NODE_TYPES = {"rule_set", "media_statement", "keyframes_statement", "at_rule", "supports_statement"}


def _node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def _symbol_name(node: Node, source_bytes: bytes) -> str:
    if node.type == "rule_set":
        selectors = node.child_by_field_name("selectors") or next(
            (c for c in node.children if c.type == "selectors"), None
        )
        if selectors is not None:
            return _node_text(selectors, source_bytes)
        return _node_text(node, source_bytes).split("{", 1)[0].strip()

    if node.type == "keyframes_statement":
        name_node = next((c for c in node.children if c.type == "keyframes_name"), None)
        name = _node_text(name_node, source_bytes) if name_node is not None else ""
        return f"@keyframes {name}".strip()

    # media_statement / at_rule / supports_statement: everything up to the
    # first block/{ is the rule's "prelude" — good enough as a human-readable
    # identifier (e.g. "@media screen and (min-width: 100px)", "@font-face")
    block = next((c for c in node.children if c.type == "block"), None)
    if block is not None:
        return _node_text(node, source_bytes)[: block.start_byte - node.start_byte].strip()
    return _node_text(node, source_bytes).split("{", 1)[0].strip()


def chunk_css(root: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    leftover: list[Node] = []

    for child in root.children:
        if child.type in _RULE_NODE_TYPES:
            chunks.append(
                Chunk(
                    file_path=file_path,
                    symbol_name=_symbol_name(child, source_bytes),
                    kind="rule",
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    code_text=_node_text(child, source_bytes),
                )
            )
        else:
            leftover.append(child)

    if leftover:
        chunks.append(
            Chunk(
                file_path=file_path,
                symbol_name=None,
                kind="module",
                start_line=leftover[0].start_point[0] + 1,
                end_line=leftover[-1].end_point[0] + 1,
                code_text="\n".join(_node_text(n, source_bytes) for n in leftover),
            )
        )

    return chunks
