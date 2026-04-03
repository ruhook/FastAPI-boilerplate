from typing import Any

from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from .const import DEFAULT_USER_PROFILE_IMAGE_URL
from .crud import crud_users
from .schema import UserCreate, UserCreateInternal, UserUpdate


def build_user_create_values(payload: UserCreate, hashed_password: str) -> UserCreateInternal:
    return UserCreateInternal(
        name=payload.name,
        username=payload.username,
        email=payload.email,
        hashed_password=hashed_password,
        profile_image_url=DEFAULT_USER_PROFILE_IMAGE_URL,
        data={},
    )


def build_user_update_values(payload: UserUpdate, existing_data: dict[str, Any] | None = None) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    values["data"] = dict(existing_data or {})
    return values


async def ensure_user_email_and_username_available(payload: UserCreate, db) -> None:
    if await crud_users.exists(db=db, email=payload.email):
        raise DuplicateValueException("Email is already registered")
    if await crud_users.exists(db=db, username=payload.username):
        raise DuplicateValueException("Username not available")


async def get_existing_user(username: str, db, schema_to_select=None):
    user = await crud_users.get(db=db, username=username, schema_to_select=schema_to_select)
    if user is None:
        raise NotFoundException("User not found")
    return user
