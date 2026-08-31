import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sleuth.api.routes import auth, chat, repos, users
from sleuth.config import Config, load_config
from sleuth.db import apply_schema, create_pool


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()

    # apply_schema() re-runs the whole schema.sql, including an ALTER TABLE
    # that needs an ACCESS EXCLUSIVE lock. It used to run on every single
    # request (inside attach_conn below) — harmless for one-shot CLI calls,
    # but under real concurrent web traffic (multiple browser fetches, or
    # more than one server process against the same DB) those lock-requiring
    # statements collide: the loser waits out the 5s lock_timeout and raises
    # psycopg.errors.LockNotAvailable, surfacing as a 500 on literally any
    # route. Running it once at process startup instead still gets a
    # schema.sql edit applied on the next server restart (the original
    # intent — see CLAUDE.md) without paying that tax, or that risk, on
    # every request.
    #
    # The pool itself (sleuth/db.py::create_pool) fixes a second, separate
    # bug: attach_conn used to call get_connection() — a blocking
    # psycopg.connect() — directly inside this `async def` lifespan/middleware.
    # That blocks the single event loop for a full network handshake on
    # every request, so concurrent requests (e.g. the frontend's 3s /repos
    # poll) visibly queue up as "(pending)" in DevTools even though nothing
    # in React is actually issuing duplicate fetches. A pool opens its
    # connections once and hands them out on a fast in-process borrow.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = create_pool(config.database_url)
        app.state.pool = pool
        conn = pool.getconn()
        try:
            apply_schema(conn)
        finally:
            pool.putconn(conn)
        yield
        pool.close()

    app = FastAPI(title="Sleuth API", lifespan=lifespan)
    app.state.config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_conn(request, call_next):
        pool = request.app.state.pool
        # getconn()/putconn() only block on a fast in-process handoff (the
        # pool pre-opens its connections), but they're still plain blocking
        # calls — run them off the event loop via to_thread rather than
        # calling them directly inside this async function, so a slow
        # moment (pool briefly exhausted, a connection being replaced)
        # can't stall every other in-flight request either.
        #
        # No explicit commit/rollback here: the pool's connections are
        # opened with autocommit=True (sleuth/db.py::create_pool) precisely
        # so nothing is ever left mid-transaction when a request ends —
        # every statement commits itself the instant it runs. That also
        # means putconn() never has to roll a stray transaction back before
        # reuse, which used to cost every GET request a second, hidden
        # round trip to Postgres on top of its actual query.
        conn = await asyncio.to_thread(pool.getconn)
        request.state.conn = conn
        request.state.config = config
        try:
            return await call_next(request)
        finally:
            await asyncio.to_thread(pool.putconn, conn)

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(repos.router)
    app.include_router(chat.router)
    return app


app = create_app()
