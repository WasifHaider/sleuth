import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from sleuth.api.auth.session import require_session
from sleuth.api.schemas import ChatOut, CreateChatIn, MessageOut, SendMessageIn
from sleuth.retrieve.answer import stream_answer
from sleuth.store import (
    DEFAULT_CHAT_TITLE,
    create_chat,
    create_message,
    delete_chat,
    derive_chat_title,
    get_chat,
    get_repo,
    list_chats,
    list_messages,
    update_chat_title,
)

router = APIRouter(dependencies=[Depends(require_session)])


def _sse_frame(event: str | None, data: str) -> str:
    # A generated token can contain a raw "\n" (e.g. after a code block's
    # opening brace). SSE data fields can't carry an embedded newline on a
    # single "data:" line — it terminates the field early and the rest is
    # silently lost — so each physical line gets its own "data:" prefix.
    prefix = f"event: {event}\n" if event else ""
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"{prefix}{lines}\n"


@router.post("/chats", response_model=ChatOut)
def create_chat_route(body: CreateChatIn, request: Request, user: dict = Depends(require_session)) -> ChatOut:
    conn = request.state.conn
    repo = get_repo(conn, body.repo_id, user_id=user["id"])
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")
    # create_chat's RETURNING already carries everything ChatOut needs (a
    # fresh chat always has message_count=0) — this used to re-fetch EVERY
    # chat for the repo via list_chats (a GROUP BY/JOIN over every chat +
    # message) just to filter back down to the one row just inserted, an
    # unbounded-cost round trip purely to re-derive data already in hand.
    chat = create_chat(conn, body.repo_id)
    conn.commit()
    return ChatOut(**chat)


@router.get("/chats", response_model=list[ChatOut])
def get_chats_route(repo_id: str, request: Request, user: dict = Depends(require_session)) -> list[ChatOut]:
    # get_repo scoped to user_id first: repo_id is a caller-supplied query
    # param, so without this check any authenticated user could list chats
    # for a repo_id they don't own just by passing it in the URL.
    if get_repo(request.state.conn, repo_id, user_id=user["id"]) is None:
        return []
    return [ChatOut(**c) for c in list_chats(request.state.conn, repo_id)]


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
def get_messages_route(chat_id: str, request: Request, user: dict = Depends(require_session)) -> list[MessageOut]:
    conn = request.state.conn
    chat = get_chat(conn, chat_id)
    if chat is None or get_repo(conn, chat["repo_id"], user_id=user["id"]) is None:
        # Same 404 either way (chat truly doesn't exist, or it exists but
        # belongs to a repo this user doesn't own) — no signal to a caller
        # that would let them tell those two cases apart.
        raise HTTPException(status_code=404, detail="chat not found")
    return [MessageOut(**m) for m in list_messages(conn, chat_id)]


@router.delete("/chats/{chat_id}")
def delete_chat_route(chat_id: str, request: Request, user: dict = Depends(require_session)) -> dict:
    conn = request.state.conn
    chat = get_chat(conn, chat_id)
    if chat is None or get_repo(conn, chat["repo_id"], user_id=user["id"]) is None:
        raise HTTPException(status_code=404, detail="chat not found")
    delete_chat(conn, chat_id)  # messages.chat_id is ON DELETE CASCADE (schema.sql)
    conn.commit()
    return {"id": chat_id, "deleted": True}


@router.post("/chat")
async def post_chat(body: SendMessageIn, request: Request, user: dict = Depends(require_session)) -> StreamingResponse:
    conn = request.state.conn
    config = request.state.config
    chat = get_chat(conn, body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    repo = get_repo(conn, chat["repo_id"], user_id=user["id"])
    if repo is None:
        raise HTTPException(status_code=404, detail="chat not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")

    create_message(conn, body.chat_id, "user", body.question)
    # First message in a chat renames it from the generic "New chat" —
    # every other message leaves the title (already set from that first
    # question) alone, matching how e.g. ChatGPT/Claude name conversations.
    new_title = None
    if chat["title"] == DEFAULT_CHAT_TITLE:
        new_title = derive_chat_title(body.question)
        update_chat_title(conn, body.chat_id, new_title)
    conn.commit()

    async def event_stream():
        collected_sources: list[dict] = []
        pending_frames: list[str] = []
        pool = request.app.state.pool
        if new_title is not None:
            yield _sse_frame("title", json.dumps({"title": new_title}))

        # stream_answer only touches its conn during retrieval (the repo
        # status check + vector search) — it never reads or writes the DB
        # again once the generation loop starts. Holding a pooled
        # connection for the whole LLM streaming duration (can run many
        # seconds on a long answer) would tie it up doing nothing, and the
        # pool only has max_size=10 connections total: a handful of
        # concurrent chats could starve every other request in the app —
        # including the unrelated repo-list polling — of a connection to
        # borrow. So this releases the connection the instant retrieval
        # finishes (on_sources fires synchronously right after
        # search_chunks and before generation starts — verified: an async
        # generator runs everything up to its first yield, including any
        # plain callback it calls, in one uninterrupted stretch, so
        # on_sources is guaranteed to have already run by the time the
        # first token comes back) and borrows a fresh one only for the
        # final persist.
        retrieval_conn = await asyncio.to_thread(pool.getconn)
        retrieval_conn_released = False

        def on_sources(results):
            nonlocal retrieval_conn_released
            collected_sources.extend(
                {
                    "file_path": r.file_path,
                    "symbol_name": r.symbol_name,
                    "kind": r.kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    # The frontend's source-click drawer needs the actual
                    # code to display — without this it would need a
                    # separate fetch-the-file endpoint just to show the
                    # exact chunk that was already retrieved and is sitting
                    # right here.
                    "code_text": r.code_text,
                    # Lets the sidebar/source-pill UI visually distinguish a
                    # documentation excerpt from real source (see
                    # sleuth/chunking.py::is_doc_path) instead of presenting
                    # both identically, the same distinction search_chunks
                    # already uses to rank code ahead of docs.
                    "is_doc": r.is_doc,
                }
                for r in results
            )
            pending_frames.append(_sse_frame("sources", json.dumps(collected_sources)))
            pool.putconn(retrieval_conn)
            retrieval_conn_released = True

        try:
            answer_parts: list[str] = []
            async for token in stream_answer(
                body.question, chat["repo_id"], retrieval_conn, config, on_sources=on_sources
            ):
                for frame in pending_frames:
                    yield frame
                pending_frames.clear()
                answer_parts.append(token)
                yield _sse_frame(None, token)

            write_conn = await asyncio.to_thread(pool.getconn)
            try:
                create_message(
                    write_conn, body.chat_id, "assistant", "".join(answer_parts), sources=collected_sources
                )
            finally:
                await asyncio.to_thread(pool.putconn, write_conn)
        finally:
            # on_sources fires before every real answer (a repo already
            # ready to chat always retrieves something, even zero results),
            # but if stream_answer raised before reaching it — the ready
            # check race, an embedder error — the connection would
            # otherwise never go back to the pool at all.
            if not retrieval_conn_released:
                await asyncio.to_thread(pool.putconn, retrieval_conn)
        yield _sse_frame("done", "{}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
