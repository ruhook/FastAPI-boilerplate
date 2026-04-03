from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import ForbiddenException, UnauthorizedException
from ...core.security import TokenType, admin_oauth2_scheme, verify_token
from ...modules.admin.admin_user.crud import crud_admin_users
from ...modules.admin.admin_user.schema import AdminUserDBRead
from ...modules.admin.role.const import ALL_ADMIN_PERMISSIONS
from ...modules.admin.role.crud import crud_roles
from ...modules.admin.role.schema import RoleRead


async def _get_admin_from_subject(
    username_or_email: str,
    db: AsyncSession,
) -> dict[str, Any] | None:
    lookup_key = "email" if "@" in username_or_email else "username"
    return await crud_admin_users.get(
        db=db,
        is_deleted=False,
        schema_to_select=AdminUserDBRead,
        **{lookup_key: username_or_email},
    )


async def get_current_admin_user(
    token: Annotated[str, Depends(admin_oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    token_data = await verify_token(token, TokenType.ACCESS)
    if token_data is None or token_data.portal != "admin":
        raise UnauthorizedException("Admin not authenticated.")

    user = await _get_admin_from_subject(token_data.username_or_email, db=db)
    if user is None:
        raise UnauthorizedException("Admin not authenticated.")

    if user["status"] != "enabled":
        raise ForbiddenException("Admin account is disabled.")

    permissions: list[str] = ALL_ADMIN_PERMISSIONS if user["is_superuser"] else []
    role_name: str | None = None
    if not user["is_superuser"] and user["role_id"] is not None:
        role = await crud_roles.get(db=db, id=user["role_id"], schema_to_select=RoleRead)
        if role and role["enabled"]:
            permissions = role["permissions"]
            role_name = role["name"]

    return {
        **user,
        "permissions": permissions,
        "role_name": role_name,
    }


async def get_current_admin_superuser(
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    if not current_admin.get("is_superuser", False):
        raise ForbiddenException("You do not have enough privileges.")
    return current_admin


def require_admin_permission(permission: str):
    async def permission_dependency(current_admin: Annotated[dict, Depends(get_current_admin_user)]) -> dict[str, Any]:
        if current_admin["is_superuser"]:
            return current_admin
        if permission not in current_admin["permissions"]:
            raise ForbiddenException(f"Missing admin permission: {permission}")
        return current_admin

    return permission_dependency
