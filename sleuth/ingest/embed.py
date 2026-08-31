import asyncio
from abc import ABC, abstractmethod

import httpx

from sleuth.http_retry import post_with_retry

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage's reduced tier for keys with no billing method on file (confirmed
# live via the API's own x-api-warning header): 3 RPM, 10K TPM. batch_size=10
# keeps a single request's tokens well under 10K even for large code chunks;
# max_concurrency=1 + requests_per_minute=3 keeps request starts >=20s apart
# regardless of how fast responses come back. Raise these once billing is
# added to the Voyage account (standard tier lifts the cap within minutes).
FREE_TIER_BATCH_SIZE = 10
FREE_TIER_MAX_CONCURRENCY = 1
FREE_TIER_REQUESTS_PER_MINUTE = 3


class Embedder(ABC):
    model_name: str
    dim: int

    @abstractmethod
    async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
        ...


class VoyageEmbedder(Embedder):
    model_name = "voyage-code-3"
    dim = 1024

    def __init__(
        self,
        api_key: str,
        batch_size: int = 128,
        max_concurrency: int = 3,
        requests_per_minute: int | None = None,
    ):
        # requests_per_minute: confirmed live (direct call to Voyage's API)
        # that a key with no billing method on file is hard-capped at 3 RPM
        # / 10K TPM (returned in the response's x-api-warning header) — not
        # a transient blip. At that cap, firing batches concurrently and
        # retrying on 429 just re-fails the same oversized/too-frequent
        # request every time; retry/backoff tuning alone can't fix a
        # request pattern that's structurally over the limit. When set,
        # this paces request *starts* (not just completions) so consecutive
        # requests are never closer together than 60/requests_per_minute
        # seconds, independent of max_concurrency. None (default) disables
        # pacing — real ingest call sites pass the tier's actual RPM.
        self.api_key = api_key
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self._pacer_lock = asyncio.Lock()
        self._last_request_started: float | None = None

    async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)
        total = len(batches)
        completed = 0

        async def run_one(client, batch):
            nonlocal completed
            vectors = await self._embed_one_batch(client, semaphore, batch)
            completed += 1
            if on_batch_done:
                on_batch_done(completed, total)
            return vectors

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(run_one(client, batch) for batch in batches))

        vectors: list[list[float]] = []
        for batch_vectors in results:
            vectors.extend(batch_vectors)
        return vectors

    async def _wait_for_pacing_slot(self) -> None:
        if not self.requests_per_minute:
            return
        min_interval = 60.0 / self.requests_per_minute
        async with self._pacer_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            if self._last_request_started is not None:
                wait = min_interval - (now - self._last_request_started)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request_started = loop.time()

    async def _embed_one_batch(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, batch: list[str]
    ) -> list[list[float]]:
        async with semaphore:
            await self._wait_for_pacing_slot()
            response = await post_with_retry(
                client,
                VOYAGE_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": batch, "model": self.model_name},
            )

        data = response.json()["data"]
        data.sort(key=lambda item: item["index"])
        return [item["embedding"] for item in data]
