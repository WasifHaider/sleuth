import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from sleuth.config import Config
from sleuth.http_retry import post_with_retry

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

DEFAULT_TIMEOUT = 60


class Generator(ABC):
    model_name: str
    timeout: float = DEFAULT_TIMEOUT

    def __init__(self, api_key: str, model_name: str | None = None):
        self.api_key = api_key
        if model_name:
            self.model_name = model_name

    @abstractmethod
    def _url(self) -> str: ...

    async def chat(self, messages: list[dict], stream: bool = True) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if stream:
                async for token in self._stream_chat(client, messages):
                    yield token
            else:
                yield await self._chat_once(client, messages)

    async def _chat_once(self, client: httpx.AsyncClient, messages: list[dict]) -> str:
        response = await post_with_retry(
            client,
            self._url(),
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_name, "messages": messages},
        )
        return response.json()["choices"][0]["message"]["content"]

    async def _stream_chat(self, client: httpx.AsyncClient, messages: list[dict]) -> AsyncIterator[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model_name, "messages": messages, "stream": True}
        async with client.stream("POST", self._url(), headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta


class GroqGenerator(Generator):
    model_name = "openai/gpt-oss-120b"

    def _url(self) -> str:
        return GROQ_URL


class NimGenerator(Generator):
    model_name = "meta/llama-3.1-70b-instruct"
    timeout = 120

    def _url(self) -> str:
        return NIM_URL


def get_generator(config: Config) -> Generator:
    if config.generation_provider == "groq":
        return GroqGenerator(api_key=config.groq_api_key, model_name=config.groq_model)
    if config.generation_provider == "nim":
        return NimGenerator(api_key=config.nim_api_key)
    raise ValueError(f"Unknown generation provider: {config.generation_provider}")


def get_fallback_chain(config: Config) -> list[Generator]:
    chain = [get_generator(config)]
    if config.generation_provider == "groq" and config.nim_api_key:
        chain.append(NimGenerator(api_key=config.nim_api_key))
    return chain


async def chat_with_fallback(
    chain: list[Generator], messages: list[dict], stream: bool = True
) -> AsyncIterator[str]:
    last_error: Exception | None = None
    for generator in chain:
        try:
            async for token in generator.chat(messages, stream=stream):
                yield token
            return
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"All generators in fallback chain failed: {last_error}")
