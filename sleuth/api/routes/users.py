from fastapi import APIRouter, Depends, Request, Response

from sleuth.api.auth.session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_cookie,
    require_session,
)
from sleuth.api.schemas import UpdateMeIn, UserOut
from sleuth.store import set_user_theme

router = APIRouter()


@router.get("/me", response_model=UserOut)
def get_me(user: dict = Depends(require_session)) -> UserOut:
    return UserOut(**user)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: UpdateMeIn, request: Request, response: Response, user: dict = Depends(require_session)
) -> UserOut:
    set_user_theme(request.state.conn, user["id"], body.theme_preference)
    request.state.conn.commit()
    updated_user = {**user, "theme_preference": body.theme_preference}
    # The session cookie carries the user's claims (see session.py) so
    # require_session never has to hit the DB — but that means a claim
    # changed here (theme_preference) would otherwise stay stale in the
    # cookie until the user's next login. Re-issuing it with the fresh
    # value keeps the change visible on the very next request.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie(updated_user, request.app.state.config),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return UserOut(**updated_user)
