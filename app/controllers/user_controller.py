"""User management. Admins only."""

from fastapi import APIRouter, Depends

from app.dependencies import get_user_service, require_admin
from app.schemas import Ok, PasswordReset, User, UserCreate
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[User])
def list_users(users: UserService = Depends(get_user_service)):
    return users.list()


@router.post("", response_model=User, status_code=201)
def create_user(
    body: UserCreate,
    admin: dict = Depends(require_admin),
    users: UserService = Depends(get_user_service),
):
    return users.create(body.username, body.password, body.role, created_by=admin["username"])


@router.delete("/{username}", response_model=Ok)
def delete_user(
    username: str,
    admin: dict = Depends(require_admin),
    users: UserService = Depends(get_user_service),
):
    """Deletes the account and logs it out everywhere. You can't delete yourself or the last admin."""
    users.delete(username, acting_user=admin["username"])
    return Ok()


@router.put("/{username}/password", response_model=Ok)
def reset_password(username: str, body: PasswordReset, users: UserService = Depends(get_user_service)):
    """Sets a new password for a user and logs them out everywhere."""
    users.set_password(username, body.password)
    return Ok()
