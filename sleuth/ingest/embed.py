import asyncio
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from pathlib import Path

import httpx

from sleuth.http_retry import post_with_retry

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage's reduced tier for keys with no billing method on file (confirmed
# live via the API's own x-api-warning header): 3 RPM, 10K TPM. Raise these
# once billing is added to the Voyage account (standard tier lifts the cap
# within minutes: 2000 RPM / 3M TPM for voyage-code-3 — confirmed against
# MongoDB/Voyage's published rate-limit docs).
#
# batch_size=10 alone used to be trusted as "keeps a single request's tokens
# well under 10K" purely from a char/4 estimate — never actually verified
# against this project's real chunks. Measured directly against SLEUTH's own
# 1990 chunks with the real voyage-code-3 tokenizer: average chunk is only
# ~201 tokens, but a handful run much denser (max 1884 tokens/chunk, 116
# chunks over 1000 tokens each) — 6 of 199 fixed-size-10 batches came in
# OVER 10K tokens (max observed: 11,547), which is exactly what a live 429 at
# batch ~12 looks like. FREE_TIER_MAX_TOKENS_PER_BATCH closes that by making
# batch boundaries token-aware, not just count-aware; FREE_TIER_TOKENS_PER_MINUTE
# backs that with an actual rolling-window pacer (see _wait_for_tpm_slot) so
# the two are enforced independently, matching Voyage's own two independent
# limits — pacing request COUNT alone (the old requests_per_minute=3 logic)
# never accounted for token VOLUME at all.
FREE_TIER_BATCH_SIZE = 10
FREE_TIER_MAX_CONCURRENCY = 1
FREE_TIER_REQUESTS_PER_MINUTE = 3
# 9000, not 10000: a safety margin below Voyage's stated 10K TPM ceiling.
# Our local tokenizer count and Voyage's own server-side count should match
# closely (same published tokenizer, per Voyage's docs), but "should match
# closely" isn't "guaranteed identical" — the margin absorbs small
# discrepancies without needing to actually observe a 429 to find them.
FREE_TIER_MAX_TOKENS_PER_BATCH = 9000
FREE_TIER_TOKENS_PER_MINUTE = 9000

# The exact tokenizer voyage-code-3 uses server-side (Voyage open-sources
# these on Hugging Face specifically so callers can count tokens accurately
# instead of guessing — see docs.voyageai.com/docs/tokenization). Loaded
# from a vendored copy (vendor/voyage-code-3-tokenizer/, see that
# directory's NOTICE.md for provenance) rather than
# Tokenizer.from_pretrained(...) at call time — confirmed live that
# from_pretrained issues a network HEAD request to huggingface.co on EVERY
# call, even with the file already cached locally, which broke respx-mocked
# tests (respx intercepts all httpx traffic) and would make every real
# ingest run silently depend on Hugging Face being reachable, a dependency
# that didn't exist before this. Cached at module level: every VoyageEmbedder
# instance shares one loaded tokenizer rather than each re-parsing the file.
_VOYAGE_TOKENIZER_PATH = Path(__file__).resolve().parent.parent.parent / "vendor" / "voyage-code-3-tokenizer" / "tokenizer.json"
_tokenizer_cache = None


def _default_token_counter(text: str) -> int:
    global _tokenizer_cache
    if _tokenizer_cache is None:
        from tokenizers import Tokenizer

        _tokenizer_cache = Tokenizer.from_file(str(_VOYAGE_TOKENIZER_PATH))
    return len(_tokenizer_cache.encode(text).ids)


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
        max_tokens_per_batch: int | None = None,
        tokens_per_minute: int | None = None,
        token_counter: Callable[[str], int] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], "asyncio.Future"] | None = None,
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
        #
        # max_tokens_per_batch / tokens_per_minute are the TPM half of the
        # same problem — previously nothing on this class counted tokens at
        # all, batch_size alone was trusted (wrongly, see FREE_TIER_* comment
        # above) to keep a batch's real token count under the limit. Both
        # None (default) disables all token-aware behavior, preserving
        # existing callers/tests that don't care about TPM.
        #
        # clock/sleep: injectable purely for deterministic tests. A genuinely
        # tight tokens_per_minute budget can force a real ~60s wait between
        # requests (correct real-world behavior for a real rate limit) —
        # exercising that in a test with the real clock/asyncio.sleep would
        # make the suite either slow or flaky. Default to the real event
        # loop clock and asyncio.sleep; every production call site leaves
        # these at their defaults.
        self.api_key = api_key
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self.max_tokens_per_batch = max_tokens_per_batch
        self.tokens_per_minute = tokens_per_minute
        self.token_counter = token_counter or _default_token_counter
        self._clock = clock or (lambda: asyncio.get_event_loop().time())
        self._sleep = sleep or asyncio.sleep
        self._pacer_lock = asyncio.Lock()
        # Rolling 60s window of request START timestamps, oldest first.
        # Previously this was a single "_last_request_started" timestamp
        # plus a fixed 60/requests_per_minute interval — that is NOT the
        # same guarantee as "no more than requests_per_minute requests in
        # any 60-second span". With exact 20s spacing (3 RPM), requests at
        # t=0, 20, 40, 60 are all within a 60-second window that includes
        # both endpoints — a real rolling-window rate limiter (which is how
        # Voyage's own 429 enforcement behaves) can count that as 4
        # requests in the trailing minute even though our old pacer
        # considered itself perfectly compliant. Confirmed live: 429s kept
        # recurring past the TPM fix, at a point where TPM budget alone
        # couldn't explain it. Tracking actual request timestamps in a
        # rolling window (same deque technique as the TPM tracker below,
        # which never had this bug) closes that gap for real instead of
        # trusting a fixed interval to be equivalent.
        self._rpm_window: deque[float] = deque()
        # Rolling 60s window of (timestamp, tokens_sent) pairs, oldest first.
        # Unlike the RPM pacer (a single "last start time" is enough because
        # request COUNT only cares about spacing), TPM needs the actual sum
        # of tokens sent in the trailing 60 seconds — a deque lets old
        # entries fall off with a single linear scan from the front each
        # time, rather than keeping a full unbounded history.
        self._tpm_window: deque[tuple[float, int]] = deque()
        # Separate lock from _pacer_lock: guards the TPM window's
        # check-then-append. Without it, two concurrent batches (whenever
        # max_concurrency > 1) could both read the current "used" total
        # before either appends its own entry, both conclude there's room,
        # and both proceed — silently exceeding tokens_per_minute despite
        # the check existing at all.
        self._tpm_lock = asyncio.Lock()

    def _make_batches(self, texts: list[str]) -> list[list[str]]:
        # Token-aware batching: a batch always respects batch_size (a
        # request-shape limit, e.g. Voyage's max texts/request) AND, when
        # max_tokens_per_batch is set, never lets a batch's summed token
        # count exceed it either — closing a batch early even if batch_size
        # hasn't been reached yet, whenever the NEXT text would push the
        # running total over budget. A single text that alone exceeds the
        # budget still gets its own one-item batch rather than being split
        # mid-text (splitting a chunk's text would corrupt its embedding
        # semantics) — Voyage's post-retry 429 handling is the backstop if
        # that one oversized request still gets rejected.
        if not self.max_tokens_per_batch:
            return [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]

        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0
        for text in texts:
            text_tokens = self.token_counter(text)
            would_exceed_tokens = current and (current_tokens + text_tokens) > self.max_tokens_per_batch
            would_exceed_count = len(current) >= self.batch_size
            if current and (would_exceed_tokens or would_exceed_count):
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(text)
            current_tokens += text_tokens
        if current:
            batches.append(current)
        return batches

    async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
        batches = self._make_batches(texts)
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
        async with self._pacer_lock:
            while True:
                now = self._clock()
                while self._rpm_window and now - self._rpm_window[0] >= 60.0:
                    self._rpm_window.popleft()
                if len(self._rpm_window) < self.requests_per_minute:
                    self._rpm_window.append(now)
                    return
                # requests_per_minute requests already sit in the trailing
                # 60s window — wait until the OLDEST of them ages out
                # rather than a fixed interval, so this genuinely never
                # exceeds requests_per_minute in any rolling 60s span.
                wait = 60.0 - (now - self._rpm_window[0])
                await self._sleep(max(wait, 0.1))

    async def _wait_for_tpm_slot(self, batch_tokens: int) -> None:
        if not self.tokens_per_minute:
            return
        async with self._tpm_lock:
            while True:
                now = self._clock()
                # Drop entries older than the trailing 60s window before
                # deciding whether there's room — an entry from 61s ago no
                # longer counts against the current minute's budget.
                while self._tpm_window and now - self._tpm_window[0][0] >= 60.0:
                    self._tpm_window.popleft()
                used = sum(tokens for _, tokens in self._tpm_window)
                if used + batch_tokens <= self.tokens_per_minute:
                    self._tpm_window.append((now, batch_tokens))
                    return
                # Not enough room yet — sleep until the oldest entry in the
                # window falls out of it, then re-check. Held under the same
                # lock (not released/reacquired per iteration): a second
                # waiter blocked behind this one must re-evaluate against
                # this waiter's own now-recorded entry too, not just race it.
                if self._tpm_window:
                    wait = 60.0 - (now - self._tpm_window[0][0])
                    await self._sleep(max(wait, 0.1))
                else:
                    # batch_tokens alone exceeds tokens_per_minute — waiting
                    # would spin forever. Let it through as a single
                    # oversized request; post_with_retry's 429 handling is
                    # the backstop.
                    self._tpm_window.append((now, batch_tokens))
                    return

    async def _embed_one_batch(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, batch: list[str]
    ) -> list[list[float]]:
        batch_tokens = sum(self.token_counter(t) for t in batch) if self.tokens_per_minute else 0

        async def on_attempt() -> None:
            # Re-acquire a pacing slot before EVERY attempt post_with_retry
            # makes, not just the first — a retry (even one honoring a real
            # Retry-After wait) is a genuinely new HTTP request against
            # Voyage's own per-minute budget. Only pacing the first attempt
            # let our own RPM/TPM bookkeeping silently drift from what
            # actually hit the server: confirmed live, 429s kept recurring
            # during a real SLEUTH ingest even after token-aware batching
            # closed the original oversized-batch cause, because retries
            # were invisible to this accounting.
            await self._wait_for_pacing_slot()
            await self._wait_for_tpm_slot(batch_tokens)

        async with semaphore:
            response = await post_with_retry(
                client,
                VOYAGE_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": batch, "model": self.model_name},
                on_attempt=on_attempt,
            )

        data = response.json()["data"]
        data.sort(key=lambda item: item["index"])
        return [item["embedding"] for item in data]
