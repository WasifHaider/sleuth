import asyncio

import httpx

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


async def post_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 1, backoff_seconds: float = 1.0, **kwargs
) -> httpx.Response:
    attempt = 0
    while True:
        try:
            response = await client.post(url, **kwargs)
        except httpx.TransportError:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(backoff_seconds)
                continue
            raise

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < retries:
            attempt += 1
            await asyncio.sleep(backoff_seconds)
            continue

        response.raise_for_status()
        return response
