import shutil
import tempfile

from sleuth.chunking import format_chunk_context
from sleuth.config import Config
from sleuth.ingest.chunk import chunk_source
from sleuth.ingest.clone import CloneError, clone_repo, list_source_files
from sleuth.ingest.embed import VoyageEmbedder
from sleuth.ingest.parse import LANGUAGES
from sleuth.store import (
    create_repo,
    delete_stale_chunks,
    get_existing_hashes,
    set_repo_embedding_info,
    update_repo_status,
    upsert_chunks,
)

SUPPORTED_EXTENSIONS = set(LANGUAGES.keys())
EXTENSION_TO_LANGUAGE = {ext: spec.key for ext, spec in LANGUAGES.items()}


def _find_or_create_repo(conn, github_url: str) -> str:
    row = conn.execute("SELECT id FROM repos WHERE github_url = %s", (github_url,)).fetchone()
    if row is not None:
        return str(row[0])
    repo_id = create_repo(conn, github_url)
    conn.commit()
    return repo_id


async def ingest_repo(github_url: str, conn, config: Config) -> str:
    repo_id = _find_or_create_repo(conn, github_url)
    update_repo_status(conn, repo_id, "indexing")
    conn.commit()

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)

    workdir = tempfile.mkdtemp(prefix="sleuth-clone-")
    try:
        try:
            repo_path = clone_repo(github_url, workdir)
        except CloneError as exc:
            update_repo_status(conn, repo_id, "failed", str(exc))
            conn.commit()
            return repo_id

        files = list_source_files(repo_path, SUPPORTED_EXTENSIONS)

        all_chunks = []
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                continue  # skip files that fail to parse, don't abort the whole index
            all_chunks.extend(chunks)

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
            vectors = await embedder.embed_batch(texts)
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)))
            conn.commit()

        set_repo_embedding_info(conn, repo_id, embedder.model_name, embedder.dim)
        conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys)
        conn.commit()

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
