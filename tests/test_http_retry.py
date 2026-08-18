import httpx
import pytest
import respx

from sleuth.http_retry import post_with_retry


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_succeeds_after_one_transient_failure():
    responses = [httpx.Response(503), httpx.Response(200, json={"ok": True})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    respx.post("https://example.test/x").mock(side_effect=handler)

    async with httpx.AsyncClient() as client:
        response = await post_with_retry(client, "https://example.test/x", backoff_seconds=0)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_raises_after_exhausting_retries():
    respx.post("https://example.test/x").mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://example.test/x", retries=1, backoff_seconds=0)

    assert len(respx.calls) == 2  # original attempt + 1 retry


@pytest.mark.asyncio
@respx.mock
async def test_post_with_retry_does_not_retry_non_transient_error():
    respx.post("https://example.test/x").mock(return_value=httpx.Response(401))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://example.test/x", retries=1, backoff_seconds=0)

    assert len(respx.calls) == 1  # no retry for a non-transient 401
