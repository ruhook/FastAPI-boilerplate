from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import DuplicateValueException
from ...core.security import get_password_hash, oauth2_scheme
from ...modules.user.crud import crud_users
from ...modules.user.schema import UserCreate, UserCreateInternal, UserRead, UserUpdate
from ...modules.user.service import build_user_create_values, build_user_update_values, ensure_user_email_and_username_available, get_existing_user
from ..dependencies import get_current_user

router = APIRouter(prefix="/user", tags=["web-users"])


@router.post("/register", response_model=UserRead, status_code=201)
async def register_user(
    request: Request, user: UserCreate, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict:
    await ensure_user_email_and_username_available(user, db)
    user_internal = build_user_create_values(user, get_password_hash(password=user.password))
    return await crud_users.create(db=db, object=user_internal, schema_to_select=UserRead)


@router.get("/me", response_model=UserRead)
async def read_current_user(request: Request, current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return current_user


@router.patch("/me")
async def patch_current_user(
    request: Request,
    values: UserUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    username = current_user["username"]
    db_user = await get_existing_user(username, db)

    if values.email is not None and values.email != db_user["email"] and await crud_users.exists(db=db, email=values.email):
        raise DuplicateValueException("Email is already registered")
    if (
        values.username is not None
        and values.username != db_user["username"]
        and await crud_users.exists(db=db, username=values.username)
    ):
        raise DuplicateValueException("Username not available")

    await crud_users.update(
        db=db,
        object=build_user_update_values(values, existing_data=db_user.get("data")),
        username=username,
    )
    return {"message": "User updated"}


@router.delete("/me")
async def erase_current_user(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    username = current_user["username"]
    await get_existing_user(username, db, schema_to_select=UserRead)
    await crud_users.delete(db=db, username=username)
    _ = token
    return {"message": "User deleted"}
