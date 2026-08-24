import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response

from sleuth.api import schemas
from sleuth.api.auth.session import SESSION_COOKIE_NAME, create_session_cookie
from sleuth.store import EmailAlreadyRegistered, create_user, get_user_by_email

router = APIRouter()

MIN_PASSWORD_LENGTH = 8


def _set_session_cookie(response: Response, user_id: str, request: Request) -> None:
    config = request.app.state.config
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie(user_id, config),
        httponly=True,
        samesite="lax",
    )


@router.post("/auth/signup", response_model=schemas.UserOut)
def signup(body: schemas.SignupIn, request: Request, response: Response) -> schemas.UserOut:
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        user = create_user(request.state.conn, body.email, password_hash, body.name)
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="email already registered")
    request.state.conn.commit()

    _set_session_cookie(response, user["id"], request)
    return schemas.UserOut(**user)


@router.post("/auth/login", response_model=schemas.UserOut)
def login(body: schemas.LoginIn, request: Request, response: Response) -> schemas.UserOut:
    user = get_user_by_email(request.state.conn, body.email)
    if user is None or not bcrypt.checkpw(body.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid email or password")

    _set_session_cookie(response, user["id"], request)
    return schemas.UserOut(**user)


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}
