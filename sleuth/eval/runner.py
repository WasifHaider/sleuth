import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sleuth.config import Config
from sleuth.ingest.embed import VoyageEmbedder
from sleuth.llm.generate import chat_with_fallback, get_fallback_chain, get_generator
from sleuth.retrieve.answer import build_prompt
from sleuth.retrieve.search import search_chunks

TOP_K = 8

JUDGE_PROMPT = (
    "You are grading a code-assistant answer against a reference answer. "
    "Score how well the produced answer matches the reference on a scale of 1-5 "
    "(5 = fully correct and complete, 1 = wrong or unrelated). "
    "Respond with ONLY the digit.\n\n"
    "Reference answer:\n{reference}\n\nProduced answer:\n{produced}"
)


@dataclass
class GoldenCase:
    question: str
    expected_files: list[str]
    expected_symbols: list[str] = field(default_factory=list)
    reference_answer: str = ""


@dataclass
class CaseResult:
    question: str
    hit: bool
    reciprocal_rank: float
    judge_score: int | None
    answer: str


def load_golden(path: str) -> tuple[str, list[GoldenCase]]:
    data = yaml.safe_load(Path(path).read_text())
    cases = [
        GoldenCase(
            question=c["question"],
            expected_files=c.get("expected_files", []),
            expected_symbols=c.get("expected_symbols", []),
            reference_answer=c.get("reference_answer", ""),
        )
        for c in data.get("cases", [])
    ]
    return data["repo"], cases


def _hit_and_rank(results, case: GoldenCase) -> tuple[bool, float]:
    for rank, r in enumerate(results, start=1):
        file_hit = r.file_path in case.expected_files
        symbol_hit = bool(case.expected_symbols) and r.symbol_name in case.expected_symbols
        if file_hit or symbol_hit:
            return True, 1.0 / rank
    return False, 0.0


def _parse_judge_score(text: str) -> int | None:
    match = re.search(r"[1-5]", text)
    return int(match.group()) if match else None


async def run_eval(golden_yaml_path: str, conn, config: Config) -> str:
    repo_id, cases = load_golden(golden_yaml_path)

    row = conn.execute("SELECT id FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None:
        raise ValueError(f"Repo {repo_id} not found")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)
    chain = get_fallback_chain(config)
    judge = get_generator(config)

    results: list[CaseResult] = []
    for case in cases:
        query_vector = (await embedder.embed_batch([case.question]))[0]
        search_results = search_chunks(conn, repo_id, query_vector, top_k=TOP_K)
        hit, rr = _hit_and_rank(search_results, case)

        prompt = build_prompt(case.question, search_results)
        answer = "".join(
            [t async for t in chat_with_fallback(chain, [{"role": "user", "content": prompt}], stream=False)]
        )

        judge_score = None
        if case.reference_answer:
            judge_text = "".join(
                [
                    t
                    async for t in judge.chat(
                        [
                            {
                                "role": "user",
                                "content": JUDGE_PROMPT.format(
                                    reference=case.reference_answer, produced=answer
                                ),
                            }
                        ],
                        stream=False,
                    )
                ]
            )
            judge_score = _parse_judge_score(judge_text)

        results.append(CaseResult(case.question, hit, rr, judge_score, answer))

    return _format_table(results)


def _format_table(results: list[CaseResult]) -> str:
    if not results:
        return "No cases to evaluate."

    hit_rate = sum(1 for r in results if r.hit) / len(results)
    mrr = sum(r.reciprocal_rank for r in results) / len(results)
    scored = [r.judge_score for r in results if r.judge_score is not None]
    avg_judge = sum(scored) / len(scored) if scored else None

    lines = [f"{'question':50s}  {'hit':5s}  {'rr':5s}  {'judge':5s}"]
    for r in results:
        judge_str = str(r.judge_score) if r.judge_score is not None else "-"
        lines.append(f"{r.question[:50]:50s}  {str(r.hit):5s}  {r.reciprocal_rank:.2f}  {judge_str:5s}")
    lines.append("")
    avg_judge_str = avg_judge if avg_judge is not None else "n/a"
    lines.append(f"hit-rate@{TOP_K}: {hit_rate:.2f}   MRR: {mrr:.2f}   avg judge: {avg_judge_str}")
    return "\n".join(lines)
