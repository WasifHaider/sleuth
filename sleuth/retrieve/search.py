from dataclasses import dataclass

import psycopg


@dataclass
class SearchResult:
    file_path: str
    symbol_name: str | None
    kind: str
    start_line: int
    end_line: int
    code_text: str
    distance: float
    is_doc: bool = False


def search_chunks(
    conn: psycopg.Connection,
    repo_id: str,
    query_embedding: list[float],
    top_k: int = 8,
    prefer_code: bool = True,
) -> list[SearchResult]:
    # prefer_code=True (the default, and what every real caller — CLI, API,
    # eval harness — actually wants) orders real source chunks (is_doc=false)
    # strictly ahead of documentation chunks (is_doc=true), and only within
    # each group by cosine distance. Without this, a docs/*.html architecture
    # write-up that happens to phrase things the way the question was asked
    # can out-score the actual implementation on raw distance alone — the
    # model then answers from prose about the code instead of the code
    # itself, with no way for a caller to tell the two apart from the
    # returned rows (confirmed live: asking an architecture question against
    # a repo with both `docs/*.html` and real source returned only doc
    # excerpts in the top 8). Docs still surface — after all code chunks —
    # so a genuinely doc-only question doesn't come back empty.
    order_by = "is_doc, distance" if prefer_code else "distance"
    rows = conn.execute(
        f"""
        SELECT file_path, symbol_name, kind, start_line, end_line, code_text,
               embedding <=> %(query)s::vector AS distance, is_doc
        FROM chunks
        WHERE repo_id = %(repo_id)s
        ORDER BY {order_by}
        LIMIT %(top_k)s
        """,
        {"query": query_embedding, "repo_id": repo_id, "top_k": top_k},
    ).fetchall()
    return [SearchResult(*row) for row in rows]
