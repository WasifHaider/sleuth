import pytest

from sleuth.summarize import summarize_repo_agentic


class FakeGenerator:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], stream: bool = True):
        self.calls.append(list(messages))
        yield self.responses.pop(0)


@pytest.mark.asyncio
async def test_summarize_repo_agentic_returns_final_answer_text(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    pass\n")
    fake = FakeGenerator(["This is a small utility script."])

    summary = await summarize_repo_agentic(str(tmp_path), config=None, generator=fake)

    assert summary == "This is a small utility script."


@pytest.mark.asyncio
async def test_summarize_repo_agentic_lets_the_model_explore_with_tools_first(tmp_path):
    (tmp_path / "app.py").write_text("def create_app():\n    pass\n")
    fake = FakeGenerator(
        [
            'TOOL: list_files {"glob": "*.py"}',
            "This project exposes a create_app() entrypoint.",
        ]
    )

    summary = await summarize_repo_agentic(str(tmp_path), config=None, generator=fake)

    assert summary == "This project exposes a create_app() entrypoint."
    # The tool call's result (from the real file on disk) must have reached
    # the model before it gave its final answer — i.e. this genuinely
    # explored the repo rather than answering blind.
    final_call_messages = fake.calls[-1]
    tool_result_messages = [m["content"] for m in final_call_messages if "app.py" in m["content"]]
    assert tool_result_messages


@pytest.mark.asyncio
async def test_summarize_repo_agentic_returns_none_when_all_generators_fail():
    import httpx

    class FailingGenerator:
        async def chat(self, messages, stream=True):
            raise httpx.HTTPStatusError("boom", request=None, response=None)
            yield ""  # pragma: no cover

    summary = await summarize_repo_agentic("/tmp", config=None, fallback_chain=[FailingGenerator()])

    # Summarization failure is non-fatal by design (matches the old
    # summarize_repo's contract) — a broken model backend must not raise
    # out of this function and fail the whole ingest over an optional
    # add-on step.
    assert summary is None
