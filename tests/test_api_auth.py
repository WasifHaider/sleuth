from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from tests.conftest import TEST_DATABASE_URL


def _config():
    return Config(
        voyage_api_key="k", groq_api_key="k", groq_model="m",
        database_url=TEST_DATABASE_URL,
        session_secret="test-secret-not-for-prod",
    )


def test_me_requires_session(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.get("/me")
    assert resp.status_code == 401


def test_signup_creates_user_and_sets_session(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": "correct horse", "name": "New User"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"
    assert resp.json()["name"] == "New User"

    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new@example.com"


def test_signup_rejects_duplicate_email(pg_conn):
    client = TestClient(create_app(_config()))
    client.post("/auth/signup", json={"email": "dup@example.com", "password": "correct horse", "name": "A"})
    resp = client.post("/auth/signup", json={"email": "dup@example.com", "password": "correct horse", "name": "B"})
    assert resp.status_code == 409


def test_signup_rejects_short_password(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.post("/auth/signup", json={"email": "short@example.com", "password": "abc", "name": "A"})
    assert resp.status_code == 400


def test_login_with_correct_password_sets_session(pg_conn):
    client = TestClient(create_app(_config()))
    client.post("/auth/signup", json={"email": "login@example.com", "password": "correct horse", "name": "A"})
    client.post("/auth/logout")

    resp = client.post("/auth/login", json={"email": "login@example.com", "password": "correct horse"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "login@example.com"

    me = client.get("/me")
    assert me.status_code == 200


def test_login_with_wrong_password_rejected(pg_conn):
    client = TestClient(create_app(_config()))
    client.post("/auth/signup", json={"email": "wrongpw@example.com", "password": "correct horse", "name": "A"})
    client.post("/auth/logout")

    resp = client.post("/auth/login", json={"email": "wrongpw@example.com", "password": "nope nope nope"})
    assert resp.status_code == 401


def test_login_with_unknown_email_rejected(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.post("/auth/login", json={"email": "ghost@example.com", "password": "correct horse"})
    assert resp.status_code == 401


def test_logout_clears_session(pg_conn):
    client = TestClient(create_app(_config()))
    client.post("/auth/signup", json={"email": "logout@example.com", "password": "correct horse", "name": "A"})
    client.post("/auth/logout")

    me = client.get("/me")
    assert me.status_code == 401
