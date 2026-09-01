"""Repo-level architecture summary generation, agentic version (Phase 4 of
the interactive-terminal-session plan — see the plan discussion in
docs/superpowers/plans; not a separate design doc).

Replaces the original single-prompt approach (a flat "file: kind symbol"
listing for every non-doc chunk, sent to the LLM in one unbounded chat
completion — see git history / docs/how-indexing-works.html for that
version) with the same AgentSession tool loop (sleuth/retrieve/
agent_session.py) that backs the interactive REPL and the one-shot
`sleuth agentic` command.

Why: the old approach's prompt size scaled linearly with repo size — a
large enough repo's repo-map string could blow past Groq's request size
limit (the 413 Payload Too Large bug). AgentSession is bounded by
MAX_ITERATIONS regardless of repo size: the model decides what's worth
looking at (list_files for structure, grep for entry points, read_file on
a README or key module) and converges to an answer within at most 6 round
trips whether the repo has 50 files or 5,000, with each tool call's output
separately capped (50 grep matches, 200 listed files, 400 read lines).

Deliberately non-fatal, matching the old summarize_repo()'s contract: any
failure (bad API key, rate limit, all fallback generators down) returns
None instead of raising, so ingest_repo's outer wrapper never fails the
whole repo over what's an optional add-on step.
"""

from sleuth.config import Config
from sleuth.llm.generate import Generator
from sleuth.retrieve.agent_session import AgentSession, AnswerEvent, UNAVAILABLE_MESSAGE

SUMMARY_QUESTION = (
    "Explore this codebase using your tools (list_files, grep, read_file) and then "
    "write a concise architecture summary: what the project is, its major "
    "components/modules and what each does, and how they likely fit together. "
    "Ground this in what you actually find — don't invent implementation details "
    "you haven't seen. 3-6 short paragraphs, no preamble."
)


async def summarize_repo_agentic(
    path: str,
    config: Config,
    generator: Generator | None = None,
    fallback_chain: list[Generator] | None = None,
) -> str | None:
    session = AgentSession(path, config=config, generator=generator, fallback_chain=fallback_chain)
    try:
        async for event in session.ask(SUMMARY_QUESTION):
            if isinstance(event, AnswerEvent):
                # AgentSession itself already turns a fully-failed fallback
                # chain into a friendly AnswerEvent rather than raising
                # (Phase 0's error handling) — that text must not be
                # mistaken for a real summary and stored as one.
                if event.text == UNAVAILABLE_MESSAGE:
                    return None
                return event.text
    except Exception:
        # Same non-fatal contract as the old summarize_repo(): anything
        # else that slips through (e.g. a bug in the tool dispatch itself,
        # an unreadable clone directory) must not raise out of this
        # optional step either — belt-and-braces alongside AgentSession's
        # own internal handling, not a replacement for it.
        return None
    return None
