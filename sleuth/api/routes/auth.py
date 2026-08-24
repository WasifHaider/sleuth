import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from sleuth.api import schemas
from sleuth.api.auth import email_link, github
from sleuth.api.auth.session import SESSION_COOKIE_NAME, create_session_cookie
from sleuth.store import get_or_create_user_by_email, get_or_create_user_by_github

router = APIRouter()

STATE_COOKIE_NAME = "gh_oauth_state"


def _set_session_cookie(response: RedirectResponse, user_id: str, request: Request) -> None:
    config = request.app.state.config
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie(user_id, config),
        httponly=True,
        samesite="lax",
    )


@router.get("/auth/github")
def github_login(request: Request) -> RedirectResponse:
    config = request.app.state.config
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(url=github.build_authorize_url(state, config))
    response.set_cookie(STATE_COOKIE_NAME, state, httponly=True, samesite="lax", max_age=600)
    return response


@router.get("/auth/github/callback")
def github_callback(code: str, state: str, request: Request) -> RedirectResponse:
    config = request.app.state.config
    expected_state = request.cookies.get(STATE_COOKIE_NAME)
    if expected_state and expected_state != state:
        raise HTTPException(status_code=400, detail="invalid oauth state")

    profile = github.exchange_code(code, config)
    user = get_or_create_user_by_github(
        request.state.conn,
        profile["github_id"],
        profile["email"],
        profile["name"],
        profile["avatar_url"],
    )
    request.state.conn.commit()

    response = RedirectResponse(url=f"{config.frontend_url}/app/repos", status_code=302)
    response.delete_cookie(STATE_COOKIE_NAME)
    _set_session_cookie(response, user["id"], request)
    return response


@router.post("/auth/email")
def request_email_link(body: schemas.EmailIn, request: Request) -> dict:
    config = request.app.state.config
    email_link.send_magic_link(body.email, str(request.base_url).rstrip("/"), config)
    # Always 200 regardless of whether the email exists — don't leak account existence.
    return {"ok": True}


@router.get("/auth/email/verify")
def verify_email_link(token: str, request: Request) -> RedirectResponse:
    config = request.app.state.config
    email = email_link.verify_magic_link_token(token, config)
    if email is None:
        raise HTTPException(status_code=400, detail="invalid or expired link")

    user = get_or_create_user_by_email(request.state.conn, email)
    request.state.conn.commit()

    response = RedirectResponse(url=f"{config.frontend_url}/app/repos", status_code=302)
    _set_session_cookie(response, user["id"], request)
    return response


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}
