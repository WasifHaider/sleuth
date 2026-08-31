import httpx
import pytest
import respx

from sleuth.ingest.embed import VoyageEmbedder


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_returns_vectors_in_input_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.2, 0.2], "index": 1},
                    {"embedding": [0.1, 0.1], "index": 0},
                ]
            },
        )

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="test-key")
    vectors = await embedder.embed_batch(["alpha", "beta"])

    assert vectors == [[0.1, 0.1], [0.2, 0.2]]


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_splits_into_batch_size_chunks():
    seen_batch_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen_batch_sizes.append(len(body["input"]))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [float(i), float(i)], "index": i}
                    for i in range(len(body["input"]))
                ]
            },
        )

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="test-key", batch_size=2, max_concurrency=2)
    texts = ["a", "b", "c", "d", "e"]
    vectors = await embedder.embed_batch(texts)

    assert len(vectors) == 5
    assert sorted(seen_batch_sizes) == [1, 2, 2]


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_sends_auth_header_and_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.5], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="secret-key")
    await embedder.embed_batch(["hello"])

    assert captured["auth"] == "Bearer secret-key"
    assert captured["body"]["model"] == "voyage-code-3"


def test_voyage_embedder_metadata():
    embedder = VoyageEmbedder(api_key="test-key")
    assert embedder.model_name == "voyage-code-3"
    assert embedder.dim == 1024


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_paces_requests_to_respect_rate_limit():
    import time

    timestamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamps.append(time.monotonic())
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="k", batch_size=1, max_concurrency=2, requests_per_minute=120)
    await embedder.embed_batch(["a", "b"])

    assert len(timestamps) == 2
    assert timestamps[1] - timestamps[0] >= 0.5


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_reports_progress_via_callback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="k", batch_size=1)
    calls = []
    await embedder.embed_batch(["a", "b"], on_batch_done=lambda done, total: calls.append((done, total)))

    assert len(calls) == 2
    assert all(total == 2 for _done, total in calls)
    assert {done for done, _total in calls} == {1, 2}
