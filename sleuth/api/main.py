from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sleuth.api.routes import auth, chat, repos, users
from sleuth.config import Config, load_config
from sleuth.db import apply_schema, get_connection


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="Sleuth API")
    app.state.config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_conn(request, call_next):
        conn = get_connection(config.database_url)
        apply_schema(conn)
        request.state.conn = conn
        request.state.config = config
        try:
            return await call_next(request)
        finally:
            conn.close()

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(repos.router)
    app.include_router(chat.router)
    return app


app = create_app()
