"""Login, logout and the current user's own account."""

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import Config, get_config
from app.dependencies import (
    SESSION_COOKIE,
    get_auth_service,
    get_current_user,
    get_session_token,
    get_user_service,
)
from app.schemas import LoginIn, LoginOut, Ok, PasswordChange, SetupStatus, User
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/setup-status", response_model=SetupStatus)
def setup_status(users: UserService = Depends(get_user_service)):
    """Lets the login page explain how to create the first admin."""
    return SetupStatus(has_users=users.has_users())


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    config: Config = Depends(get_config),
    auth: AuthService = Depends(get_auth_service),
):
    client_ip = request.client.host if request.client else "unknown"
    token, user = auth.login(body.username, body.password, client_ip)
    max_age = int(config.session_ttl_hours * 3600)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=config.cookie_secure,
        samesite="strict",
        path="/",
    )
    return LoginOut(user=User(**user), token=token, expires_in=max_age)


@router.post("/logout", response_model=Ok)
def logout(request: Request, response: Response, auth: AuthService = Depends(get_auth_service)):
    auth.logout(get_session_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Ok()


@router.get("/me", response_model=User)
def me(user: dict = Depends(get_current_user)):
    return User(**user)


@router.post("/password", response_model=Ok)
def change_password(
    body: PasswordChange,
    user: dict = Depends(get_current_user),
    users: UserService = Depends(get_user_service),
):
    """Change your own password. Your other sessions are logged out."""
    users.change_own_password(user["username"], body.current_password, body.new_password, user["session_hash"])
    return Ok()
