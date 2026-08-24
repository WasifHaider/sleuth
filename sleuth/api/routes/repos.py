from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from sleuth.api.auth.session import require_session
from sleuth.api.schemas import AddRepoIn, RepoOut
from sleuth.db import get_connection
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import create_repo, get_repo, list_repos

router = APIRouter(dependencies=[Depends(require_session)])


async def _run_ingest(github_url: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    try:
        await ingest_repo(github_url, conn, config)
    finally:
        conn.close()


@router.post("/repos", response_model=RepoOut)
def add_repo(body: AddRepoIn, request: Request, background_tasks: BackgroundTasks) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url)
    conn.commit()
    background_tasks.add_task(_run_ingest, body.github_url, config.database_url, config)
    return RepoOut(**get_repo(conn, repo_id))


@router.get("/repos", response_model=list[RepoOut])
def get_repos(request: Request) -> list[RepoOut]:
    conn = request.state.conn
    return [
        RepoOut(**get_repo(conn, repo_id))
        for repo_id, _github_url, _status in list_repos(conn)
    ]


@router.get("/repos/{repo_id}", response_model=RepoOut)
def get_repo_by_id(repo_id: str, request: Request) -> RepoOut:
    repo = get_repo(request.state.conn, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return RepoOut(**repo)
