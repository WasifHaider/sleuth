from collections.abc import AsyncIterator

from sleuth.config import Config
from sleuth.ingest.embed import VoyageEmbedder
from sleuth.llm.generate import chat_with_fallback, get_fallback_chain
from sleuth.retrieve.search import SearchResult, search_chunks

SYSTEM_PROMPT = (
    "You are a code assistant. Answer the user's question about the repository "
    "using only the provided code excerpts. If the excerpts don't contain the "
    "answer, say so explicitly rather than guessing."
)


def build_prompt(question: str, results: list[SearchResult]) -> str:
    blocks = []
    for r in results:
        symbol = r.symbol_name or "(module level)"
        blocks.append(
            f"# File: {r.file_path}\n# {r.kind}: {symbol} (lines {r.start_line}-{r.end_line})\n\n{r.code_text}"
        )
    context = "\n\n---\n\n".join(blocks)
    return f"Question: {question}\n\nRelevant code:\n\n{context}"


async def stream_answer(
    question: str, repo_id: str, conn, config: Config, on_sources=None
) -> AsyncIterator[str]:
    row = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None or row[0] != "ready":
        raise ValueError(f"Repo {repo_id} is not ready to query (status={row[0] if row else 'missing'})")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)
    query_vector = (await embedder.embed_batch([question]))[0]
    results = search_chunks(conn, repo_id, query_vector)
    if on_sources:
        on_sources(results)
    prompt = build_prompt(question, results)

    chain = get_fallback_chain(config)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    async for token in chat_with_fallback(chain, messages, stream=True):
        yield token


async def get_answer(question: str, repo_id: str, conn, config: Config) -> str:
    return "".join([token async for token in stream_answer(question, repo_id, conn, config)])
