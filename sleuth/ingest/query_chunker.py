from tree_sitter import Node, Query, QueryCursor

from sleuth.chunking import Chunk


def _node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def _span_key(node: Node) -> tuple[int, int]:
    return (node.start_byte, node.end_byte)


def run_query(query: Query, root: Node, source_bytes: bytes, file_path: str) -> list[Chunk]:
    # tree-sitter>=0.25 moved match execution off Query itself onto a separate
    # QueryCursor object (Query.matches(root) was removed) — construct one
    # per call rather than caching, since a cursor also carries mutable
    # per-run state (byte/point range limits, match-limit tracking) that
    # should not leak between unrelated calls sharing the same cached Query.
    matches = QueryCursor(query).matches(root)

    method_records = []
    for _, captures in matches:
        if "definition.method" not in captures:
            continue
        outer = captures["definition.method"][0]
        inner = captures.get("inner.method", [outer])[0]
        class_name = _node_text(captures["name.class"][0], source_bytes)
        method_name = _node_text(captures["name.method"][0], source_bytes)
        method_records.append(
            {
                "outer": outer,
                "key": _span_key(inner),
                "symbol_name": f"{class_name}.{method_name}",
                "kind": "method",
            }
        )

    excluded_function_keys = {r["key"] for r in method_records}
    classes_with_methods = {
        _span_key(captures["definition.class_context"][0])
        for _, captures in matches
        if "definition.method" in captures
    }

    function_candidates: dict[tuple[int, int], dict] = {}
    class_candidates: dict[tuple[int, int], dict] = {}

    for _, captures in matches:
        if "definition.method" in captures or "definition.class_context" in captures:
            continue

        if "definition.function" in captures:
            outer = captures["definition.function"][0]
            inner = captures.get("inner.function", [outer])[0]
            key = _span_key(inner)
            if key in excluded_function_keys:
                continue
            _keep_largest(
                function_candidates,
                key,
                outer=outer,
                symbol_name=_node_text(captures["name.function"][0], source_bytes),
                kind="function",
            )
        elif "definition.class" in captures:
            outer = captures["definition.class"][0]
            inner = captures.get("inner.class", [outer])[0]
            key = _span_key(inner)
            _keep_largest(
                class_candidates,
                key,
                outer=outer,
                symbol_name=_node_text(captures["name.class"][0], source_bytes),
                kind="class",
            )

    records = (
        method_records
        + list(function_candidates.values())
        + [r for key, r in class_candidates.items() if key not in classes_with_methods]
    )

    # a class-with-methods is consumed at the root even though it emits no
    # whole-class chunk of its own — methods represent it instead
    top_level_keys = {_span_key(r["outer"]) for r in function_candidates.values()}
    top_level_keys |= {_span_key(r["outer"]) for r in class_candidates.values()}

    chunks = [
        Chunk(
            file_path=file_path,
            symbol_name=r["symbol_name"],
            kind=r["kind"],
            start_line=r["outer"].start_point[0] + 1,
            end_line=r["outer"].end_point[0] + 1,
            code_text=_node_text(r["outer"], source_bytes),
        )
        for r in records
    ]

    leftover = [child for child in root.children if _span_key(child) not in top_level_keys]
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


def _keep_largest(
    candidates: dict[tuple[int, int], dict], key: tuple[int, int], *, outer: Node, symbol_name: str, kind: str
) -> None:
    span = outer.end_byte - outer.start_byte
    existing = candidates.get(key)
    if existing is None or span > existing["span"]:
        candidates[key] = {"outer": outer, "symbol_name": symbol_name, "kind": kind, "span": span}
