from fastapi import APIRouter, Depends, Request

from sleuth.api.auth.session import require_session
from sleuth.api.schemas import UpdateMeIn, UserOut
from sleuth.store import set_user_theme

router = APIRouter()


@router.get("/me", response_model=UserOut)
def get_me(user: dict = Depends(require_session)) -> UserOut:
    return UserOut(**user)


@router.patch("/me", response_model=UserOut)
def update_me(body: UpdateMeIn, request: Request, user: dict = Depends(require_session)) -> UserOut:
    set_user_theme(request.state.conn, user["id"], body.theme_preference)
    request.state.conn.commit()
    return UserOut(**{**user, "theme_preference": body.theme_preference})
