import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response

from sleuth.api import schemas
from sleuth.api.auth.session import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_cookie
from sleuth.store import EmailAlreadyRegistered, create_user, get_user_by_email

router = APIRouter()

MIN_PASSWORD_LENGTH = 8
# Cost factor 12 (bcrypt's default) measured at ~0.6-0.9s per hash on this
# hardware — the dominant cost of every login/signup call. 11 (~0.2-0.25s)
# keeps a healthy margin above the widely-used cost-10 baseline while
# roughly halving that latency; still exponentially harder to brute-force
# than 10, just not gratuitously slower than it needs to be for this app's
# threat model.
BCRYPT_ROUNDS = 11
# Used only to keep login's timing constant when the email doesn't exist —
# see login() below. Any fixed valid-shaped bcrypt hash works; this one is
# never checked against a real password.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"dummy-constant-time-comparison", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def _set_session_cookie(response: Response, user: dict, request: Request) -> None:
    config = request.app.state.config
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie(user, config),
        httponly=True,
        # Frontend (Vercel) and backend (this API, behind Cloudflare Tunnel)
        # are different registrable domains in every real deployment — the
        # browser only ever sends a cookie on a cross-site fetch when it's
        # SameSite=None, and SameSite=None requires Secure (HTTPS-only) or
        # browsers reject the cookie outright. "lax" (the old value) is
        # only sent same-site, so it silently never reached the backend
        # once frontend/backend split across domains: login would set the
        # cookie fine, but the very next /me call had no cookie to read,
        # surfacing as an inexplicable 401 right after a successful login.
        # Both localhost:5717 (dev, http) and the real deployment (https)
        # need this to work — a plain hardcoded "none"/secure=True pair
        # would break local dev, since Secure cookies are dropped outright
        # over http. frontend_origin's own scheme decides which mode is
        # correct for the environment actually running.
        samesite="none" if config.frontend_origin.startswith("https://") else "lax",
        secure=config.frontend_origin.startswith("https://"),
        max_age=SESSION_MAX_AGE_SECONDS,
    )


@router.post("/auth/signup", response_model=schemas.UserOut)
def signup(body: schemas.SignupIn, request: Request, response: Response) -> schemas.UserOut:
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")
    try:
        user = create_user(request.state.conn, body.email, password_hash, body.name)
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="email already registered")
    request.state.conn.commit()

    _set_session_cookie(response, user, request)
    return schemas.UserOut(**user)


@router.post("/auth/login", response_model=schemas.UserOut)
def login(body: schemas.LoginIn, request: Request, response: Response) -> schemas.UserOut:
    user = get_user_by_email(request.state.conn, body.email)
    # Always run bcrypt.checkpw, even when the email doesn't exist — hashing
    # is the expensive part of this call (~0.2-0.25s at BCRYPT_ROUNDS=11), so
    # short-circuiting it for an unknown email makes that request measurably
    # faster than one for a real email with a wrong password. That timing
    # gap is a real side-channel: it lets an attacker enumerate which emails
    # are registered by timing responses alone, with no error message
    # needed. Checking against a fixed dummy hash keeps both paths' cost
    # the same regardless of which one 401s.
    password_hash = user["password_hash"] if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = bcrypt.checkpw(body.password.encode("utf-8"), password_hash.encode("utf-8"))
    if user is None or not password_ok:
        raise HTTPException(status_code=401, detail="invalid email or password")

    _set_session_cookie(response, user, request)
    return schemas.UserOut(**user)


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}
