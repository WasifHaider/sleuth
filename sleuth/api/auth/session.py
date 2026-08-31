from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from sleuth.config import Config
from sleuth.store import get_user

SESSION_COOKIE_NAME = "sleuth_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _serializer(config: Config) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt="session")


def create_session_cookie(user: dict, config: Config) -> str:
    # Embeds the claims a route actually needs (email/name/theme_preference),
    # not just the user id, so require_session below can verify+read a
    # request's identity from the signed cookie alone — no DB round trip on
    # the common path. This was the actual point of using a signed token in
    # the first place; a token that only carries a user_id and still forces
    # a DB lookup on every request gets the cost of a stateful session with
    # none of the benefit of a stateless one.
    return _serializer(config).dumps(
        {
            "user_id": user["id"],
            "email": user.get("email"),
            "name": user.get("name"),
            "theme_preference": user.get("theme_preference", "storm"),
        }
    )


def read_session_claims(cookie_value: str, config: Config) -> dict | None:
    try:
        payload = _serializer(config).loads(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload


def require_session(request: Request) -> dict:
    config: Config = request.app.state.config
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    claims = read_session_claims(cookie_value, config) if cookie_value else None
    if claims is None or not claims.get("user_id"):
        raise HTTPException(status_code=401, detail="not authenticated")

    # A cookie signed before this claims-embedding change carries only
    # user_id — fall back to the old DB lookup so existing sessions don't
    # get logged out by this upgrade. Any cookie issued from here on
    # (login/signup/PATCH /me) always carries the full claims, so this
    # branch is a one-time migration path, not the steady-state cost.
    if "email" not in claims:
        user = get_user(request.state.conn, claims["user_id"])
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return user

    return {
        "id": claims["user_id"],
        "email": claims.get("email"),
        "name": claims.get("name"),
        "theme_preference": claims.get("theme_preference", "storm"),
    }
