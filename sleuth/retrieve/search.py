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


def search_chunks(
    conn: psycopg.Connection,
    repo_id: str,
    query_embedding: list[float],
    top_k: int = 8,
) -> list[SearchResult]:
    rows = conn.execute(
        """
        SELECT file_path, symbol_name, kind, start_line, end_line, code_text,
               embedding <=> %(query)s::vector AS distance
        FROM chunks
        WHERE repo_id = %(repo_id)s
        ORDER BY distance
        LIMIT %(top_k)s
        """,
        {"query": query_embedding, "repo_id": repo_id, "top_k": top_k},
    ).fetchall()
    return [SearchResult(*row) for row in rows]
