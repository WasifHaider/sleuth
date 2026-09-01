"""One-shot agentic Q&A: sleuth agentic <path> <question>.

Thin backward-compatible wrapper around AgentSession (sleuth/retrieve/
agent_session.py) — a single question, one throwaway session, exits after
the answer. All the actual tool-call machinery (text-protocol parsing,
grep/list_files/read_file tools, fallback-chain generation) lives in
agent_session.py now, shared with the interactive REPL (sleuth/repl.py).
"""

from collections.abc import AsyncIterator

from sleuth.config import Config
from sleuth.llm.generate import Generator
from sleuth.retrieve.agent_session import AgentSession, AnswerEvent


async def run_agentic(
    question: str,
    path: str,
    config: Config,
    generator: Generator | None = None,
    fallback_chain: list[Generator] | None = None,
) -> AsyncIterator[str]:
    session = AgentSession(path, config=config, generator=generator, fallback_chain=fallback_chain)
    async for event in session.ask(question):
        if isinstance(event, AnswerEvent):
            if event.truncated:
                yield event.text + "\n\n(Note: search was cut short after reaching the iteration limit.)"
            else:
                yield event.text
