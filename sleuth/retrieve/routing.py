"""Cheap keyword-based question routing (Phase 2 of the global/architecture
retrieval plan). No LLM call by design — a heuristic false positive just
means a normal question gets the summary prepended too, which is harmless;
a false negative just means a broad question falls back to plain top-k
search, today's existing behavior. Either failure mode is safe, so a fast
keyword check beats spending an extra LLM round-trip on every question to
classify it."""

import re

_GLOBAL_PATTERNS = [
    r"\barchitecture\b",
    r"\boverall\b",
    r"\bwhole (project|repo|repository|codebase)\b",
    r"\bentire (project|repo|repository|codebase)\b",
    r"\bsummarize (the )?(whole|everything|this repo|this project)\b",
    r"\brate (my|this) (architecture|codebase|project|design)\b",
    r"\bhigh.level (overview|summary|design)\b",
    r"\bwhat does this (project|repo|codebase) do\b",
]
_GLOBAL_RE = re.compile("|".join(_GLOBAL_PATTERNS), re.IGNORECASE)


def classify_question(question: str) -> str:
    return "global" if _GLOBAL_RE.search(question) else "local"
