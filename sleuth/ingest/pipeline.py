import shutil
import tempfile

from sleuth.chunking import format_chunk_context
from sleuth.config import Config
from sleuth.ingest.chunk import chunk_source
from sleuth.ingest.clone import CloneError, clone_repo, list_source_files
from sleuth.ingest.embed import (
    FREE_TIER_BATCH_SIZE,
    FREE_TIER_MAX_CONCURRENCY,
    FREE_TIER_REQUESTS_PER_MINUTE,
    VoyageEmbedder,
)
from sleuth.ingest.parse import LANGUAGES
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
    upsert_repo_summary,
)
from sleuth.summarize import summarize_repo_agentic

SUPPORTED_EXTENSIONS = set(LANGUAGES.keys())
EXTENSION_TO_LANGUAGE = {ext: spec.key for ext, spec in LANGUAGES.items()}


def _find_or_create_repo(conn, github_url: str) -> str:
    row = conn.execute("SELECT id FROM repos WHERE github_url = %s", (github_url,)).fetchone()
    if row is not None:
        return str(row[0])
    repo_id = create_repo(conn, github_url)
    conn.commit()
    return repo_id


async def ingest_repo(github_url: str, conn, config: Config, on_event=None, repo_id: str | None = None) -> str:
    def emit(step: str, **detail) -> None:
        if on_event:
            on_event(step, detail)

    if repo_id is None:
        repo_id = _find_or_create_repo(conn, github_url)
    update_repo_status(conn, repo_id, "indexing")
    conn.commit()
    emit("cloning")

    # ingest_repo's documented contract (CLAUDE.md) is "never raises —
    # failures land in repos.status = 'failed'". Only clone_repo()'s
    # CloneError was actually caught below; anything past that point —
    # embedder.embed_batch() hitting Voyage (network error, bad/expired
    # API key, rate limit exhausted after http_retry's one retry),
    # upsert_chunks()/other store calls hitting Postgres, even a bug in
    # chunk_source() — propagated straight out uncaught. Since the only
    # caller (repos.py::_run_ingest, a FastAPI BackgroundTask) has no
    # try/except of its own either, that exception vanished into
    # BackgroundTasks' internal logging with the repo permanently stuck at
    # status='indexing': no error shown, no retry button ever appears,
    # because IndexingScreen's isFailed only lights up once status is
    # actually 'failed'. Wrapping the whole body closes that gap for real,
    # instead of only for the one failure mode (clone) that happened to be
    # handled already.
    try:
        return await _run_ingest_steps(github_url, conn, config, repo_id, emit)
    except Exception as exc:
        update_repo_status(conn, repo_id, "failed", str(exc))
        conn.commit()
        emit("failed", error=str(exc))
        return repo_id


async def _run_ingest_steps(github_url: str, conn, config: Config, repo_id: str, emit) -> str:
    embedder = VoyageEmbedder(
        api_key=config.voyage_api_key,
        batch_size=FREE_TIER_BATCH_SIZE,
        max_concurrency=FREE_TIER_MAX_CONCURRENCY,
        requests_per_minute=FREE_TIER_REQUESTS_PER_MINUTE,
    )

    workdir = tempfile.mkdtemp(prefix="sleuth-clone-")
    try:
        try:
            repo_path = clone_repo(github_url, workdir)
        except CloneError as exc:
            update_repo_status(conn, repo_id, "failed", str(exc))
            conn.commit()
            emit("failed", error=str(exc))
            return repo_id

        files = list_source_files(repo_path, SUPPORTED_EXTENSIONS)
        emit("cloned", files=len(files))

        all_chunks = []
        skipped = 0
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                skipped += 1
                continue  # skip files that fail to parse, don't abort the whole index
            all_chunks.extend(chunks)
        emit("parsed", parsed=len(files) - skipped, skipped=skipped)
        emit("chunked", chunks=len(all_chunks))

        # Summarization failure is deliberately non-fatal: a flaky/rate-
        # limited Groq call here must not block local-search chat (the
        # thing that actually works today) from ever becoming available.
        # ingest_repo's outer wrapper would otherwise catch this and mark
        # the WHOLE repo failed over what's really an optional add-on.
        #
        # Uses the agentic tool loop (AgentSession, via
        # summarize_repo_agentic) against the cloned repo directory on
        # disk rather than a flat "file: kind symbol" listing sent in one
        # unbounded prompt — that older approach's prompt size scaled
        # linearly with repo size and could blow past Groq's request size
        # limit on a large enough repo (413 Payload Too Large). The
        # agentic version is bounded by AgentSession's MAX_ITERATIONS
        # regardless of repo size: the model decides what's worth
        # exploring (list_files/grep/read_file) and converges within a
        # fixed number of round trips whether the repo has 50 files or
        # 5,000.
        try:
            summary = await summarize_repo_agentic(str(repo_path), config)
            if summary:
                upsert_repo_summary(conn, repo_id, summary)
                conn.commit()
                emit("summarized")
        except Exception as exc:
            emit("summary_failed", error=str(exc))

        current_keys = {(c.file_path, c.symbol_name) for c in all_chunks}
        existing_hashes = get_existing_hashes(conn, repo_id)

        to_embed = [
            c for c in all_chunks
            if existing_hashes.get((c.file_path, c.symbol_name)) != c.content_hash
        ]

        if to_embed:
            texts = [
                format_chunk_context(c, EXTENSION_TO_LANGUAGE.get("." + c.file_path.rsplit(".", 1)[-1], ""))
                for c in to_embed
            ]
            emit("embedding_start", to_embed=len(to_embed))
            vectors = await embedder.embed_batch(
                texts,
                on_batch_done=lambda done, total: emit("embedding_progress", done=done, total=total),
            )
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)))
            conn.commit()

        set_repo_embedding_info(conn, repo_id, embedder.model_name, embedder.dim)
        conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys)
        conn.commit()
        emit("stored", upserted=len(to_embed), skipped_unchanged=len(all_chunks) - len(to_embed))

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        emit("ready")
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
