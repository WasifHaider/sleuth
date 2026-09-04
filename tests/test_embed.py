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


def test_make_batches_splits_on_token_budget_not_just_count():
    # Real bug this covers: batch_size alone (a fixed chunk COUNT) was
    # trusted to keep every batch under Voyage's 10K TPM ceiling, based on
    # an assumed ~1000 tokens/chunk average. Measured against SLEUTH's own
    # real chunks with the real voyage-code-3 tokenizer, that assumption was
    # false — some batches came in over 11K tokens and 429'd. A fake counter
    # here (no network/model download needed) simulates one oversized text
    # among several small ones, and asserts the oversized one gets its own
    # batch rather than being lumped in with others past the token budget.
    def fake_counter(text: str) -> int:
        return len(text)  # 1 "token" per char, for a controllable test

    embedder = VoyageEmbedder(
        api_key="k", batch_size=10, max_tokens_per_batch=10, token_counter=fake_counter
    )
    # "aaaaa" (5) + "bbbbb" (5) = 10, fits one batch exactly.
    # "cccccccccccc" (12) alone exceeds the 10-token budget -> its own batch.
    # "d" (1) starts a fresh batch after the oversized one.
    batches = embedder._make_batches(["aaaaa", "bbbbb", "cccccccccccc", "d"])

    assert batches == [["aaaaa", "bbbbb"], ["cccccccccccc"], ["d"]]


def test_make_batches_without_token_budget_falls_back_to_count_only():
    embedder = VoyageEmbedder(api_key="k", batch_size=2)
    batches = embedder._make_batches(["a", "b", "c", "d", "e"])
    assert batches == [["a", "b"], ["c", "d"], ["e"]]


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_paces_tokens_to_respect_tpm_limit():
    # Real rolling-window TPM pacer, not just RPM: two batches whose combined
    # tokens exceed tokens_per_minute must be spaced apart until the first
    # batch's tokens age out of the trailing 60s window. Uses an injected
    # fake clock/sleep (see VoyageEmbedder's clock/sleep params) rather than
    # a real 60-second wait or a fragile short-real-sleep tolerance — the
    # fake clock advances by exactly however long each requested "sleep" was,
    # so the assertions are exact, not timing-sensitive.
    timestamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamps.append(fake_now[0])
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    fake_now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    embedder = VoyageEmbedder(
        api_key="k",
        batch_size=1,
        max_concurrency=2,
        tokens_per_minute=10,
        token_counter=lambda text: 8,
        clock=lambda: fake_now[0],
        sleep=fake_sleep,
    )
    # Two batches at 8 "tokens" each; a 10-token budget can only fit one
    # until the first entry (recorded at t=0) ages out of the 60s window.
    await embedder.embed_batch(["a", "b"])

    assert len(timestamps) == 2
    assert min(timestamps) == 0.0
    # The second request only became possible once the first entry aged
    # past the 60s window boundary.
    assert max(timestamps) >= 60.0


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_lets_single_oversized_batch_through_without_hanging():
    # A single text whose own token count already exceeds tokens_per_minute
    # must not spin forever waiting for room that can never free up —
    # confirmed no timeout/hang, the batch is simply let through as-is.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(
        api_key="k", batch_size=1, tokens_per_minute=10, token_counter=lambda text: 999
    )
    vectors = await embedder.embed_batch(["huge"])
    assert vectors == [[0.1]]


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_retries_re_acquire_pacing_slot_not_just_first_attempt():
    # Real bug this covers: post_with_retry's internal retries (5 by
    # default, honoring Retry-After) previously ran completely outside the
    # RPM/TPM pacer's view — only the FIRST attempt of a batch acquired a
    # slot, so a retried request (a genuinely new HTTP call against
    # Voyage's own per-minute budget) never got counted, letting our
    # accounting silently drift from what actually hit the server. This
    # was the real explanation for a live SLEUTH ingest continuing to hit
    # 429s well past the original oversized-batch fix. Here, the FIRST
    # attempt returns a 429; the retry should ALSO wait for the rolling
    # RPM window before being sent, not fire immediately.
    fake_now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    attempt_times = []
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        attempt_times.append(fake_now[0])
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(
        api_key="k",
        batch_size=1,
        max_concurrency=1,
        requests_per_minute=1,
        clock=lambda: fake_now[0],
        sleep=fake_sleep,
    )
    vectors = await embedder.embed_batch(["a"])

    assert vectors == [[0.1]]
    # Two real network attempts: the initial 429 and the successful retry.
    assert len(attempt_times) == 2
    # The retry is a SECOND request against a requests_per_minute=1 budget
    # already consumed by the first attempt at t=0 — it must wait for that
    # first slot to age out of the 60s window before being sent, not fire
    # back-to-back just because it's "a retry".
    assert attempt_times[1] >= 60.0


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_paces_requests_to_respect_rate_limit():
    # With requests_per_minute=120 and only 2 requests, both comfortably
    # fit in the rolling 60s window with room to spare (2 << 120) — a real
    # rolling-window rate limiter (see _wait_for_pacing_slot) has no reason
    # to force any artificial spacing between them at all. This replaces an
    # earlier version of this test that asserted a fixed ~0.5s gap
    # (60/120s) between the two requests — that was the OLD, buggy
    # fixed-interval pacer's behavior, not a real rate-limit requirement;
    # seeing the current rolling-window pacer actually forced to wait is
    # covered separately by
    # test_embed_batch_rpm_pacer_uses_real_rolling_window_not_fixed_interval.
    import time

    timestamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamps.append(time.monotonic())
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(api_key="k", batch_size=1, max_concurrency=2, requests_per_minute=120)
    await embedder.embed_batch(["a", "b"])

    assert len(timestamps) == 2


@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_rpm_pacer_uses_real_rolling_window_not_fixed_interval():
    # Real bug this covers: the RPM pacer used to track a single
    # "last request started" timestamp plus a fixed 60/RPM interval — that
    # is NOT equivalent to "no more than RPM requests in any 60-second
    # window". With a fixed interval, requests at t=0, 20, 40, 60 (RPM=3)
    # are all spaced exactly 20s apart yet land 4-in-60-seconds at the
    # instant t=60 lands — a real rolling-window limiter (which is how
    # Voyage's actual 429 enforcement behaves) can reject that 4th request.
    # A THIRD request here (RPM=2) must wait for the FIRST one to age out
    # of the 60s window, not just wait a fixed interval since the second.
    fake_now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    timestamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamps.append(fake_now[0])
        return httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})

    respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=handler)

    embedder = VoyageEmbedder(
        api_key="k",
        batch_size=1,
        max_concurrency=1,
        requests_per_minute=2,
        clock=lambda: fake_now[0],
        sleep=fake_sleep,
    )
    await embedder.embed_batch(["a", "b", "c"])

    assert len(timestamps) == 3
    assert timestamps[0] == 0.0
    # Second request fits immediately (only 1 in the window so far).
    assert timestamps[1] == 0.0
    # Third request must wait until the FIRST request's timestamp (t=0)
    # ages out of the 60s window, i.e. until t>=60 — not just some short
    # fixed interval since the second request.
    assert timestamps[2] >= 60.0


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
