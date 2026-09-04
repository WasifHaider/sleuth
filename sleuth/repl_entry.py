"""Standalone entry point for the pip-installable `sleuth-repl` package.

This is the ONLY thing the trimmed-down `sleuth-repl` distribution exposes
(see pyproject.toml's [project.scripts]) — it drives the interactive
agentic REPL (sleuth/repl.py -> sleuth/retrieve/agent_session.py) against
a local directory, using a Groq API key the user supplies once via
sleuth/local_config.py.

Deliberately does NOT expose `add`/`list`/`ask`/`eval` (sleuth/cli.py's
Postgres+Voyage-backed commands) — those stay a monorepo-only workflow.
Nothing in this module ever opens a database connection or calls Voyage;
AgentSession's tool loop (grep/list_files/read_file) only ever touches the
local filesystem, and generation goes straight to Groq with the user's own
key (see sleuth/llm/generate.py) — never through any of this project's
own infrastructure, so this package needs zero secrets baked in and adds
zero cost to running it against a stranger's machine.
"""

import argparse
import asyncio

from sleuth.config import Config
from sleuth.local_config import resolve_groq_api_key
from sleuth.repl import run_repl

# Never actually read by the agentic path — AgentSession hardcodes its own
# model (AGENTIC_GROQ_MODEL in sleuth/retrieve/agent_session.py), by
# explicit product decision: this package doesn't let a user pick a model.
_UNUSED_GROQ_MODEL_PLACEHOLDER = "unused-agentic-mode-has-its-own-model-constant"


def _build_config(groq_api_key: str) -> Config:
    # voyage_api_key/database_url are inert placeholders: Config is the
    # same frozen dataclass shared with the full monorepo CLI, but nothing
    # this package runs ever reads those two fields (no DB connection, no
    # embedding call is reachable from AgentSession's tool loop).
    return Config(
        voyage_api_key="unused",
        groq_api_key=groq_api_key,
        groq_model=_UNUSED_GROQ_MODEL_PLACEHOLDER,
        database_url="unused",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sleuth",
        description="Interactive agentic Q&A over a local codebase.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="directory to investigate (default: current directory)",
    )
    return parser


async def _main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    groq_api_key = resolve_groq_api_key()
    config = _build_config(groq_api_key)
    await run_repl(args.path, config)


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
