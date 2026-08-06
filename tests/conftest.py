import os

import pytest

from sleuth.db import apply_schema, get_connection

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/sleuth"
)


@pytest.fixture
def pg_conn():
    conn = get_connection(TEST_DATABASE_URL)
    apply_schema(conn)
    conn.execute("TRUNCATE repos CASCADE")
    conn.commit()
    yield conn
    conn.close()
