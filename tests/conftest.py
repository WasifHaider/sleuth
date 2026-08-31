import os

import pytest
from dotenv import load_dotenv

from sleuth.db import apply_schema, create_pool

load_dotenv()

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:***@localhost:5433/sleuth"
)


@pytest.fixture(scope="session")
def _pg_pool():
    # One pool, opened once for the whole test session, instead of every
    # single test paying for its own psycopg.connect() — each of those is a
    # real blocking TCP+TLS handshake (see db.py::create_pool's comment),
    # and against a real remote Postgres (Supabase, now that
    # TEST_DATABASE_URL points at the same instance as DATABASE_URL rather
    # than a local Docker container on loopback) that handshake cost is
    # real, cross-region latency multiplied by every test in the suite.
    # min_size=1 is enough — the suite runs tests sequentially, not in
    # parallel, so there's never more than one connection borrowed at once.
    pool = create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    yield pool
    pool.close()


@pytest.fixture
def pg_conn(_pg_pool):
    with _pg_pool.connection() as conn:
        apply_schema(conn)
        conn.execute("TRUNCATE repos, users CASCADE")
        yield conn
