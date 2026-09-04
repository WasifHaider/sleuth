import asyncio
import random

import httpx

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


def _sleep_seconds(response: httpx.Response | None, attempt: int, base_backoff: float, max_backoff: float) -> float:
    # A flat 1s wait was nowhere near enough for a REAL rate limit: Voyage's
    # per-minute quota needs tens of seconds to clear, not one second, and
    # a fixed wait ignores the server's own Retry-After hint entirely (a
    # 429 response is required by spec to be allowed to carry one, and
    # Voyage's actually does). Prefer the server's stated wait time when
    # present; otherwise fall back to exponential backoff with jitter
    # (jitter spreads out concurrent callers retrying in lockstep, which
    # would otherwise all slam the API again at the exact same instant).
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(float(retry_after), max_backoff)
            except ValueError:
                pass
    exponential = base_backoff * (2**attempt)
    return min(exponential, max_backoff) * (0.5 + random.random())


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 5,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    on_attempt=None,
    **kwargs,
) -> httpx.Response:
    # retries=5 (was 1): a single retry after a flat 1s wait can maybe
    # survive a transient 5xx blip, but a REAL 429 from a provider's
    # per-minute rate limit needs the caller to actually wait out most of
    # that minute — confirmed live: ingesting a real repo's embedding batch
    # hit Voyage's rate limit and the old retries=1/1s config gave up and
    # raised straight through ingest_repo's failure path long before the
    # limit could plausibly have cleared.
    #
    # on_attempt: optional async callback awaited immediately before EVERY
    # attempt (the first send AND every retry). Added because a caller's
    # own client-side rate-limit pacer (see VoyageEmbedder's RPM/TPM
    # windows in sleuth/ingest/embed.py) previously only ran once, before
    # the FIRST attempt — a retry that fires minutes later (honoring a
    # real Retry-After header) is a genuinely new HTTP request against the
    # same per-minute budget, but the pacer's own bookkeeping had already
    # recorded that request as consumed at the ORIGINAL attempt's
    # timestamp. That drift between "when we think budget was spent" and
    # "when the request actually landed on the server" compounds across a
    # long batch run and was the real explanation for a live SLEUTH ingest
    # continuing to hit 429s well past a token-aware batching fix. Calling
    # on_attempt before every real network attempt keeps the pacer's
    # accounting honest for retries, not just first tries. None (default)
    # is a no-op, preserving every existing caller that doesn't pace at all
    # (generation's fallback-chain retries, which intentionally give up
    # fast rather than pace, per this function's own retries=1 override
    # there).
    attempt = 0
    while True:
        if on_attempt is not None:
            await on_attempt()
        try:
            response = await client.post(url, **kwargs)
        except httpx.TransportError:
            if attempt < retries:
                await asyncio.sleep(_sleep_seconds(None, attempt, backoff_seconds, max_backoff_seconds))
                attempt += 1
                continue
            raise

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < retries:
            await asyncio.sleep(_sleep_seconds(response, attempt, backoff_seconds, max_backoff_seconds))
            attempt += 1
            continue

        response.raise_for_status()
        return response
