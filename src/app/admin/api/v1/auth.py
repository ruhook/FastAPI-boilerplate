from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user
from ....core.db.database import async_get_db
from ....core.exceptions.http_exceptions import UnauthorizedException
from ....core.security import TokenType, admin_oauth2_scheme, verify_token
from ....modules.admin.admin_user.crud import crud_admin_users
from ....modules.admin.admin_user.schema import (
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminRefreshRequest,
    AdminToken,
    AdminUserAuth,
)
from ....modules.admin.admin_user.service import login_admin_user, refresh_admin_user_tokens
from ....modules.admin.role.const import ALL_ADMIN_PERMISSIONS

router = APIRouter(prefix="/auth", tags=["admin-auth"])


@router.post("/login", response_model=AdminToken)
async def admin_login(
    payload: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AdminToken:
    return await login_admin_user(payload=payload, db=db, all_permissions=ALL_ADMIN_PERMISSIONS)


@router.post("/refresh", response_model=AdminToken)
async def admin_refresh(
    payload: AdminRefreshRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AdminToken:
    token_data = await verify_token(payload.refresh_token, TokenType.REFRESH)
    if token_data is None or token_data.portal != "admin":
        raise UnauthorizedException("Invalid refresh token.")
    admin_user = await crud_admin_users.get(db=db, username=token_data.username_or_email, is_deleted=False)
    return await refresh_admin_user_tokens(
        admin_user=admin_user,
        refresh_token=payload.refresh_token,
        db=db,
        all_permissions=ALL_ADMIN_PERMISSIONS,
    )


@router.get("/me", response_model=AdminUserAuth)
async def read_current_admin(
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return current_admin


@router.post("/logout")
async def admin_logout(
    payload: AdminLogoutRequest,
    access_token: Annotated[str, Depends(admin_oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    _ = payload, access_token, db
    return {"message": "Logged out successfully."}
