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
    **kwargs,
) -> httpx.Response:
    # retries=5 (was 1): a single retry after a flat 1s wait can maybe
    # survive a transient 5xx blip, but a REAL 429 from a provider's
    # per-minute rate limit needs the caller to actually wait out most of
    # that minute — confirmed live: ingesting a real repo's embedding batch
    # hit Voyage's rate limit and the old retries=1/1s config gave up and
    # raised straight through ingest_repo's failure path long before the
    # limit could plausibly have cleared.
    attempt = 0
    while True:
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
