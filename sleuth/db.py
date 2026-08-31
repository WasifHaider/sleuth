import time
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
LOCK_TIMEOUT = "5s"


def get_connection(
    database_url: str, *, connect_timeout: int = 5, retries: int = 1, backoff_seconds: float = 0.5
) -> psycopg.Connection:
    attempt = 0
    while True:
        try:
            conn = psycopg.connect(database_url, autocommit=True, connect_timeout=connect_timeout)
            register_vector(conn)
            conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            return conn
        except psycopg.OperationalError:
            if attempt >= retries:
                raise
            attempt += 1
            time.sleep(backoff_seconds)


def _configure_pooled_connection(conn: psycopg.Connection) -> None:
    # Runs once per physical connection the pool opens (not once per
    # borrow), so register_vector/lock_timeout only pay their setup cost
    # min_size..max_size times total, not once per HTTP request.
    register_vector(conn)
    conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    # A per-request `psycopg.connect(...)` (get_connection above) is a real,
    # blocking TCP+TLS handshake out to Postgres (Supabase in prod is a
    # cross-region round trip). Doing that inside `async def` FastAPI
    # middleware blocks the single event loop for the full handshake, so
    # every other in-flight request — including ones the frontend is
    # actively polling, like GET /repos every 3s — queues up behind it and
    # shows as "(pending)" in DevTools. A pool opens `min_size` connections
    # once at startup and hands existing ones out on borrow, so a request
    # only waits on a fast in-process handoff, not a fresh network connect.
    #
    # autocommit=True is the other half of the fix. A `psycopg` connection
    # defaults to autocommit=False, so every conn.execute() — even a
    # read-only SELECT — opens an implicit transaction. A route that only
    # reads (GET /repos, GET /me's underlying lookup, etc.) never calls
    # commit() because it has nothing to write, so the connection goes back
    # to the pool still mid-transaction; the pool then has to roll it back
    # itself before the next borrow can reuse it. Each of those — the
    # original query, then the rollback — is its own network round trip to
    # Postgres, so every "one query" GET request was actually paying for
    # two round trips (measured ~0.7-0.8s instead of the ~0.2s a single
    # round trip costs against this project's Supabase instance). With
    # autocommit=True each statement commits itself immediately, so there's
    # no transaction left open to clean up and no second round trip.
    # Existing conn.commit() calls throughout the routes/store/pipeline
    # code become harmless no-ops (verified: a commit() on an autocommit
    # connection never touches the network) rather than needing to be torn
    # out — every write path here is already single-statement-per-commit,
    # not relying on grouping multiple statements into one transaction, so
    # nothing loses atomicity it was actually using.
    return ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True},
        configure=_configure_pooled_connection,
        open=True,
    )


def apply_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.execute(sql)
