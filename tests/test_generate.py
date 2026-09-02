import json

import httpx
import pytest
import respx

from sleuth.config import Config
from sleuth.llm.generate import (
    GroqGenerator,
    NimGenerator,
    chat_with_fallback,
    get_fallback_chain,
    get_generator,
)


@pytest.mark.asyncio
@respx.mock
async def test_groq_generator_streams_tokens():
    sse = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse.encode())
    )

    generator = GroqGenerator(api_key="k", model_name="test-model")
    tokens = [t async for t in generator.chat([{"role": "user", "content": "hi"}], stream=True)]

    assert "".join(tokens) == "Hello world"


@pytest.mark.asyncio
@respx.mock
async def test_groq_generator_non_streaming_returns_full_text():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "full answer"}}]})

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=handler)

    generator = GroqGenerator(api_key="k", model_name="test-model")
    tokens = [t async for t in generator.chat([{"role": "user", "content": "hi"}], stream=False)]

    assert tokens == ["full answer"]


@pytest.mark.asyncio
@respx.mock
async def test_chat_with_fallback_fails_over_to_nim_on_persistent_groq_failure():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429)
    )
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "nim answer"}}]})
    )

    chain = [GroqGenerator(api_key="k", model_name="m"), NimGenerator(api_key="k")]
    tokens = [
        t async for t in chat_with_fallback(chain, [{"role": "user", "content": "hi"}], stream=False)
    ]

    assert tokens == ["nim answer"]


def test_get_generator_and_fallback_chain():
    config = Config(
        voyage_api_key="vk",
        groq_api_key="gk",
        groq_model="test-model",
        database_url="unused",
        generation_provider="groq",
        nim_api_key="nk",
    )

    generator = get_generator(config)
    assert isinstance(generator, GroqGenerator)
    assert generator.model_name == "test-model"

    chain = get_fallback_chain(config)
    assert isinstance(chain[0], GroqGenerator)
    assert isinstance(chain[1], NimGenerator)


def test_get_fallback_chain_without_nim_key_is_primary_only():
    config = Config(
        voyage_api_key="vk",
        groq_api_key="gk",
        groq_model="test-model",
        database_url="unused",
        generation_provider="groq",
        nim_api_key=None,
    )

    chain = get_fallback_chain(config)
    assert len(chain) == 1


@pytest.mark.asyncio
@respx.mock
async def test_chat_with_fallback_error_includes_exception_type_when_message_empty():
    # httpx.TransportError subclasses (timeouts, connection resets) can carry
    # an empty str() when the underlying socket/SSL error has no message
    # text — a real failure observed running `sleuth eval` against a
    # rate-limited provider: the RuntimeError raised was
    # "All generators in fallback chain failed: " with nothing after the
    # colon, indistinguishable from a bug. The error must always name at
    # least the exception TYPE, even when its message is blank.
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("")
    )

    chain = [GroqGenerator(api_key="k", model_name="m")]

    with pytest.raises(RuntimeError) as exc_info:
        async for _ in chat_with_fallback(chain, [{"role": "user", "content": "hi"}], stream=False):
            pass

    assert "ConnectError" in str(exc_info.value)
