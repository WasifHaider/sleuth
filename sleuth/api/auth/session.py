from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from sleuth.config import Config
from sleuth.store import get_user

SESSION_COOKIE_NAME = "sleuth_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _serializer(config: Config) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt="session")


def create_session_cookie(user_id: str, config: Config) -> str:
    return _serializer(config).dumps({"user_id": user_id})


def read_session_cookie(cookie_value: str, config: Config) -> str | None:
    try:
        payload = _serializer(config).loads(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload.get("user_id")


def require_session(request: Request) -> dict:
    config: Config = request.app.state.config
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_cookie(cookie_value, config) if cookie_value else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")

    user = get_user(request.state.conn, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user
