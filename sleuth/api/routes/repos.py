import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from sleuth.api import progress_store
from sleuth.api.auth.session import require_session
from sleuth.api.schemas import AddRepoIn, RepoOut
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import create_repo, delete_repo, get_repo, list_repos_full, update_repo_status

router = APIRouter(dependencies=[Depends(require_session)])


async def _run_ingest(repo_id: str, github_url: str, pool, config) -> None:
    # Runs as a FastAPI BackgroundTask (still on the event loop, `async def`),
    # outside the request/response cycle attach_conn manages, so it borrows
    # its own connection from the same pool the app opened at startup —
    # via to_thread, same reasoning as attach_conn, since getconn/putconn are
    # still plain blocking calls even though the pool makes them fast.
    conn = await asyncio.to_thread(pool.getconn)
    try:
        progress_store.start(repo_id)
        try:
            await ingest_repo(
                github_url, conn, config, repo_id=repo_id,
                on_event=lambda step, detail: progress_store.record(repo_id, step, **detail),
            )
        except Exception as exc:
            # ingest_repo (sleuth/ingest/pipeline.py) already catches
            # everything inside its own body and marks the repo 'failed'
            # on the way out — this is a second, narrower safety net for
            # anything outside that body: progress_store.start/record
            # themselves, or a bug in ingest_repo's own exception handler.
            # FastAPI's BackgroundTasks has no supervisor of its own — an
            # uncaught exception here is silently logged and the repo would
            # otherwise be stuck at whatever status it last reached, with
            # nothing in the UI ever explaining why indexing stopped.
            update_repo_status(conn, repo_id, "failed", str(exc))
            progress_store.record(repo_id, "failed", error=str(exc))
    finally:
        await asyncio.to_thread(pool.putconn, conn)


@router.post("/repos", response_model=RepoOut)
def add_repo(
    body: AddRepoIn, request: Request, background_tasks: BackgroundTasks,
    user: dict = Depends(require_session),
) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url, user_id=user["id"])
    conn.commit()
    background_tasks.add_task(_run_ingest, repo_id, body.github_url, request.app.state.pool, config)
    return RepoOut(**get_repo(conn, repo_id, user_id=user["id"]))


@router.get("/repos", response_model=list[RepoOut])
def get_repos(request: Request, user: dict = Depends(require_session)) -> list[RepoOut]:
    return [RepoOut(**repo) for repo in list_repos_full(request.state.conn, user["id"])]


@router.get("/repos/{repo_id}", response_model=RepoOut)
def get_repo_by_id(repo_id: str, request: Request, user: dict = Depends(require_session)) -> RepoOut:
    repo = get_repo(request.state.conn, repo_id, user_id=user["id"])
    if repo is None:
        # Same 404 whether the repo doesn't exist at all or exists but
        # belongs to someone else — a 403 (or any different response)
        # would let a caller distinguish "not mine" from "doesn't exist"
        # and use that to enumerate other users' repo ids.
        raise HTTPException(status_code=404, detail="repo not found")
    return RepoOut(**repo)


@router.get("/repos/{repo_id}/progress")
def get_progress(repo_id: str, request: Request, user: dict = Depends(require_session)) -> dict:
    repo = get_repo(request.state.conn, repo_id, user_id=user["id"])
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    progress = progress_store.get(repo_id)
    if progress is None:
        return {"step": repo["status"], "detail": {}, "log": [], "elapsed_seconds": 0}
    return progress


@router.post("/repos/{repo_id}/retry", response_model=RepoOut)
def retry_repo(
    repo_id: str, request: Request, background_tasks: BackgroundTasks,
    user: dict = Depends(require_session),
) -> RepoOut:
    # A retry must re-run ingestion against the SAME repo row, not create a
    # new one — this used to just be the frontend calling POST /repos again
    # with the same github_url, which create_repo() happily inserted as a
    # brand new row every time (no uniqueness constraint on github_url),
    # leaving a duplicate entry in the repo list every time someone retried
    # a failed index. A dedicated retry endpoint that takes the existing
    # repo_id closes that off entirely: there is no code path here that can
    # create a second row for the same repo.
    conn = request.state.conn
    config = request.state.config
    repo = get_repo(conn, repo_id, user_id=user["id"])
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    update_repo_status(conn, repo_id, "pending")
    conn.commit()
    background_tasks.add_task(_run_ingest, repo_id, repo["github_url"], request.app.state.pool, config)
    return RepoOut(**get_repo(conn, repo_id, user_id=user["id"]))


@router.delete("/repos/{repo_id}")
def delete_repo_route(repo_id: str, request: Request, user: dict = Depends(require_session)) -> dict:
    conn = request.state.conn
    repo = get_repo(conn, repo_id, user_id=user["id"])
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    # ON DELETE CASCADE (schema.sql: chunks/repo_summaries/chats, and
    # messages cascading off chats) takes every chat and message for this
    # repo with it — deleting a repo is meant to remove its conversations
    # too, not leave them orphaned pointing at a repo_id that no longer
    # resolves to anything.
    delete_repo(conn, repo_id)
    conn.commit()
    return {"id": repo_id, "deleted": True}
