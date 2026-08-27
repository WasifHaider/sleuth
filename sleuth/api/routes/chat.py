import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from sleuth.api.auth.session import require_session
from sleuth.api.schemas import ChatOut, CreateChatIn, MessageOut, SendMessageIn
from sleuth.db import get_connection
from sleuth.retrieve.answer import stream_answer
from sleuth.store import create_chat, create_message, get_chat, get_repo, list_chats, list_messages

router = APIRouter(dependencies=[Depends(require_session)])


@router.post("/chats", response_model=ChatOut)
def create_chat_route(body: CreateChatIn, request: Request) -> ChatOut:
    conn = request.state.conn
    repo = get_repo(conn, body.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")
    chat_id = create_chat(conn, body.repo_id)
    conn.commit()
    return ChatOut(**[c for c in list_chats(conn, body.repo_id) if c["id"] == chat_id][0])


@router.get("/chats", response_model=list[ChatOut])
def get_chats_route(repo_id: str, request: Request) -> list[ChatOut]:
    return [ChatOut(**c) for c in list_chats(request.state.conn, repo_id)]


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
def get_messages_route(chat_id: str, request: Request) -> list[MessageOut]:
    conn = request.state.conn
    if get_chat(conn, chat_id) is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return [MessageOut(**m) for m in list_messages(conn, chat_id)]


@router.post("/chat")
async def post_chat(body: SendMessageIn, request: Request) -> StreamingResponse:
    conn = request.state.conn
    config = request.state.config
    chat = get_chat(conn, body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    repo = get_repo(conn, chat["repo_id"])
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")

    create_message(conn, body.chat_id, "user", body.question)
    conn.commit()

    async def event_stream():
        collected_sources: list[dict] = []
        pending_frames: list[str] = []

        def on_sources(results):
            collected_sources.extend(
                {
                    "file_path": r.file_path,
                    "symbol_name": r.symbol_name,
                    "kind": r.kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                }
                for r in results
            )
            pending_frames.append(f"event: sources\ndata: {json.dumps(collected_sources)}\n\n")

        # The per-request `conn` is closed by attach_conn's middleware as soon as
        # call_next() returns, which happens before a StreamingResponse's body
        # actually streams — so this generator needs a connection of its own,
        # scoped to its own lifetime (same reasoning as Task 2's background ingest task).
        stream_conn = get_connection(config.database_url)
        try:
            answer_parts: list[str] = []
            async for token in stream_answer(
                body.question, chat["repo_id"], stream_conn, config, on_sources=on_sources
            ):
                for frame in pending_frames:
                    yield frame
                pending_frames.clear()
                answer_parts.append(token)
                yield f"data: {token}\n\n"

            create_message(stream_conn, body.chat_id, "assistant", "".join(answer_parts), sources=collected_sources)
            stream_conn.commit()
        finally:
            stream_conn.close()
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
