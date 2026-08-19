import time

import psycopg
import pytest

from sleuth.db import apply_schema, get_connection
from tests.conftest import TEST_DATABASE_URL


def test_get_connection_retries_once_on_transient_connect_failure(monkeypatch):
    real_connect = psycopg.connect
    calls = []

    def flaky_connect(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError("connection timed out")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", flaky_connect)

    conn = get_connection(TEST_DATABASE_URL)

    assert len(calls) == 2
    conn.close()


def test_get_connection_raises_after_persistent_connect_failure(monkeypatch):
    def always_fails(*args, **kwargs):
        raise psycopg.OperationalError("connection timed out")

    monkeypatch.setattr(psycopg, "connect", always_fails)

    with pytest.raises(psycopg.OperationalError):
        get_connection(TEST_DATABASE_URL)


def test_apply_schema_creates_tables():
    conn = get_connection(TEST_DATABASE_URL)
    apply_schema(conn)

    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    table_names = {row[0] for row in rows}

    assert "repos" in table_names
    assert "chunks" in table_names
    conn.close()


def test_connection_lock_timeout_fails_fast_instead_of_hanging():
    blocker = get_connection(TEST_DATABASE_URL)
    apply_schema(blocker)
    blocker.execute("LOCK TABLE repos IN ACCESS EXCLUSIVE MODE")

    waiter = get_connection(TEST_DATABASE_URL)
    start = time.monotonic()
    with pytest.raises(psycopg.errors.LockNotAvailable):
        waiter.execute("SELECT 1 FROM repos")
    elapsed = time.monotonic() - start

    assert elapsed < 8, "lock_timeout should abort the wait well before a real hang"
    waiter.rollback()
    waiter.close()
    blocker.rollback()
    blocker.close()
