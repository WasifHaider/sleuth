from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_connection(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, autocommit=False)
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.execute(sql)
    conn.commit()
