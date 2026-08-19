import time
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
LOCK_TIMEOUT = "5s"


def get_connection(
    database_url: str, *, connect_timeout: int = 5, retries: int = 1, backoff_seconds: float = 0.5
) -> psycopg.Connection:
    attempt = 0
    while True:
        try:
            conn = psycopg.connect(database_url, autocommit=False, connect_timeout=connect_timeout)
            register_vector(conn)
            conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            conn.commit()
            return conn
        except psycopg.OperationalError:
            if attempt >= retries:
                raise
            attempt += 1
            time.sleep(backoff_seconds)


def apply_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.execute(sql)
    conn.commit()
