from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from ....core.auth_sessions import (
    InvalidRefreshTokenError,
    RefreshSessionError,
    RefreshTokenExpiredError,
    RefreshTokenReplayError,
    revoke_refresh_token,
    rotate_refresh_session,
)
from ....core.db.database import async_get_db
from ....core.exceptions.http_exceptions import UnauthorizedException
from ....core.security import TokenType, admin_oauth2_scheme, verify_token
from ....modules.admin.admin_user.crud import crud_admin_users
from ....modules.admin.admin_user.schema import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminRefreshRequest,
    AdminToken,
    AdminUserAuth,
)
from ....modules.admin.admin_user.service import (
    change_current_admin_password,
    is_local_dev_auto_login_admin,
    issue_local_dev_auto_login_admin_tokens,
    login_admin_user,
    refresh_admin_user_tokens,
)
from ....modules.admin.role.const import ALL_ADMIN_PERMISSIONS
from ..dependencies import get_current_admin_user

router = APIRouter(prefix="/auth", tags=["admin-auth"])


@router.post("/login", response_model=AdminToken)
async def admin_login(
    request: Request,
    payload: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AdminToken:
    return await login_admin_user(
        payload=payload,
        db=db,
        all_permissions=ALL_ADMIN_PERMISSIONS,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/refresh", response_model=AdminToken)
async def admin_refresh(
    request: Request,
    payload: AdminRefreshRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AdminToken | JSONResponse:
    token_data = await verify_token(payload.refresh_token, TokenType.REFRESH)
    if (
        token_data is not None
        and token_data.portal == "admin"
        and token_data.account_id == 0
        and is_local_dev_auto_login_admin("HaokangImport")
    ):
        return await issue_local_dev_auto_login_admin_tokens(db, ALL_ADMIN_PERMISSIONS)
    try:
        rotated = await rotate_refresh_session(
            db,
            payload.refresh_token,
            portal="admin",
            user_agent=request.headers.get("user-agent"),
        )
    except (RefreshTokenExpiredError, RefreshTokenReplayError):
        return JSONResponse(status_code=401, content={"detail": "Invalid refresh token."})
    except (InvalidRefreshTokenError, RefreshSessionError):
        raise UnauthorizedException("Invalid refresh token.") from None
    admin_user = await crud_admin_users.get(
        db=db,
        id=rotated.session.account_id,
        is_deleted=False,
    )
    return await refresh_admin_user_tokens(
        admin_user=admin_user,
        refresh_token=rotated.token,
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
    _ = access_token
    token_data = await verify_token(payload.refresh_token, TokenType.REFRESH)
    is_local_refresh = (
        token_data is not None
        and token_data.portal == "admin"
        and token_data.account_id == 0
        and is_local_dev_auto_login_admin("HaokangImport")
    )
    if not is_local_refresh:
        await revoke_refresh_token(db, payload.refresh_token, portal="admin")
    return {"message": "Logged out successfully."}


@router.post("/change-password")
async def admin_change_password(
    payload: AdminChangePasswordRequest,
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    return await change_current_admin_password(payload=payload, current_admin=current_admin, db=db)
