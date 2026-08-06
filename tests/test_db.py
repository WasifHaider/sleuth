from sleuth.db import apply_schema, get_connection
from tests.conftest import TEST_DATABASE_URL


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
