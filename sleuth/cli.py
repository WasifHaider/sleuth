import argparse
import asyncio
import sys

from sleuth.config import load_config
from sleuth.db import apply_schema, get_connection
from sleuth.eval.runner import run_eval
from sleuth.ingest.pipeline import ingest_repo
from sleuth.retrieve.agentic import run_agentic
from sleuth.retrieve.answer import stream_answer
from sleuth.store import list_repos


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sleuth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("github_url")

    subparsers.add_parser("list")

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("repo_id")
    ask_parser.add_argument("question")

    agentic_parser = subparsers.add_parser("agentic")
    agentic_parser.add_argument("path")
    agentic_parser.add_argument("question")

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("golden_yaml_path")

    return parser


async def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    config = load_config()

    if args.command == "agentic":
        async for chunk in run_agentic(args.question, args.path, config):
            print(chunk, end="")
        print()
        return

    conn = get_connection(config.database_url)
    apply_schema(conn)
    try:
        if args.command == "add":
            repo_id = await ingest_repo(args.github_url, conn, config)
            status = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()[0]
            print(f"{repo_id}\t{status}")
        elif args.command == "list":
            for repo_id, github_url, status in list_repos(conn):
                print(f"{repo_id}\t{github_url}\t{status}")
        elif args.command == "ask":
            async for token in stream_answer(args.question, args.repo_id, conn, config):
                print(token, end="")
            print()
        elif args.command == "eval":
            table = await run_eval(args.golden_yaml_path, conn, config)
            print(table)
    finally:
        conn.close()


def run() -> None:
    asyncio.run(main(sys.argv[1:]))
