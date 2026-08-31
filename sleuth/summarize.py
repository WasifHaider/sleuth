"""Repo-level architecture summary generation (Phase 1 of the
global/architecture-question retrieval plan, see
docs/superpowers/plans/2026-08-29-global-architecture-question-retrieval.md).

Deliberately built from a "repo map" — file path / kind / symbol name for
every non-doc chunk — instead of full source text or a hierarchical
per-directory pass. Keeps the prompt small regardless of repo size and
needs exactly one LLM call per ingest. See the Phase 1+2+5 implementation
plan's "Deliberate simplification" section for why, and when this should
become a real map-reduce (Phase 1b) instead.
"""

SUMMARY_SYSTEM_PROMPT = (
    "You are analyzing a codebase's file/symbol listing (not the source "
    "code itself). Write a concise architecture summary: what the project "
    "is, its major components/modules and what each does, and how they "
    "likely fit together. Base this only on the file paths, symbol names, "
    "and kinds given — do not invent implementation details you can't see. "
    "3-6 short paragraphs, no preamble."
)


def build_repo_map(chunks) -> str:
    lines = []
    for c in chunks:
        if c.is_doc:
            continue
        symbol = c.symbol_name or "(module level)"
        lines.append(f"{c.file_path}: {c.kind} {symbol}")
    return "\n".join(lines)


async def summarize_repo(chunks, generator) -> str | None:
    if not chunks:
        return None
    repo_map = build_repo_map(chunks)
    if not repo_map:
        return None  # every chunk was a doc chunk — nothing to summarize
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": repo_map},
    ]
    return "".join([token async for token in generator.chat(messages, stream=False)])
