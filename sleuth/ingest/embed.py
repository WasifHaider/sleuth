import asyncio
from abc import ABC, abstractmethod

import httpx

from sleuth.http_retry import post_with_retry

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"


class Embedder(ABC):
    model_name: str
    dim: int

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class VoyageEmbedder(Embedder):
    model_name = "voyage-code-3"
    dim = 1024

    def __init__(self, api_key: str, batch_size: int = 128, max_concurrency: int = 5):
        self.api_key = api_key
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(self._embed_one_batch(client, semaphore, batch) for batch in batches)
            )

        vectors: list[list[float]] = []
        for batch_vectors in results:
            vectors.extend(batch_vectors)
        return vectors

    async def _embed_one_batch(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, batch: list[str]
    ) -> list[list[float]]:
        async with semaphore:
            response = await post_with_retry(
                client,
                VOYAGE_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": batch, "model": self.model_name},
            )

        data = response.json()["data"]
        data.sort(key=lambda item: item["index"])
        return [item["embedding"] for item in data]
