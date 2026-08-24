import httpx
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from tests.conftest import TEST_DATABASE_URL


def _config():
    return Config(
        voyage_api_key="k", groq_api_key="k", groq_model="m",
        database_url=TEST_DATABASE_URL,
        github_client_id="gh_id", github_client_secret="gh_secret",
        session_secret="test-secret-not-for-prod",
        smtp_host="localhost", smtp_port=1025, smtp_username="u",
        smtp_password="p", smtp_from_address="noreply@example.com",
    )


def test_me_requires_session(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.get("/me")
    assert resp.status_code == 401


@respx.mock
def test_github_callback_creates_user_and_sets_session(pg_conn):
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "gh_token"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={
            "id": 12345, "login": "octocat", "name": "The Octocat",
            "email": "octocat@example.com", "avatar_url": "https://avatars/o.png",
        })
    )
    client = TestClient(create_app(_config()))
    resp = client.get("/auth/github/callback", params={"code": "abc", "state": "xyz"}, follow_redirects=False)
    assert resp.status_code in (302, 307)
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["name"] == "The Octocat"


def test_email_magic_link_round_trip(pg_conn, monkeypatch):
    sent = {}

    def fake_send(email, base_url, config=None):
        sent["email"] = email
        sent["base_url"] = base_url

    monkeypatch.setattr("sleuth.api.auth.email_link.send_magic_link", fake_send)

    client = TestClient(create_app(_config()))
    resp = client.post("/auth/email", json={"email": "person@example.com"})
    assert resp.status_code == 200
    assert sent["email"] == "person@example.com"

    from sleuth.api.auth.email_link import _serializer

    token = _serializer(client.app.state.config).dumps("person@example.com")
    verify = client.get("/auth/email/verify", params={"token": token}, follow_redirects=False)
    assert verify.status_code in (302, 307)
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"
