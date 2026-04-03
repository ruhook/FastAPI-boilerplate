from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from ..admin_user.model import AdminUser
from .crud import crud_roles
from .schema import RoleCreate, RoleCreateInternal, RoleRead, RoleUpdate


def build_role_create_values(payload: RoleCreate) -> RoleCreateInternal:
    return RoleCreateInternal(
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        permissions=payload.permissions,
        data={},
    )


def build_role_update_values(payload: RoleUpdate, existing_data: dict[str, Any] | None = None) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    values["data"] = dict(existing_data or {})
    return values


async def list_roles(db: AsyncSession) -> list[dict[str, Any]]:
    roles = await crud_roles.get_multi(db=db, limit=1000)
    return roles["data"]


async def create_role(payload: RoleCreate, db: AsyncSession) -> dict[str, Any]:
    if await crud_roles.exists(db=db, name=payload.name):
        raise DuplicateValueException("Role name already exists.")
    created_role = await crud_roles.create(
        db=db,
        object=build_role_create_values(payload),
        schema_to_select=RoleRead,
        return_as_model=True,
    )
    return created_role.model_dump()


async def get_role(role_id: int, db: AsyncSession) -> dict[str, Any]:
    role = await crud_roles.get(db=db, id=role_id, schema_to_select=RoleRead)
    if role is None:
        raise NotFoundException("Role not found.")
    return role


async def update_role(role_id: int, payload: RoleUpdate, db: AsyncSession) -> dict[str, Any]:
    role = await get_role(role_id, db)
    if payload.name and payload.name != role["name"] and await crud_roles.exists(db=db, name=payload.name):
        raise DuplicateValueException("Role name already exists.")
    await crud_roles.update(
        db=db,
        object={**build_role_update_values(payload, existing_data=role.get("data")), "updated_at": datetime.now(UTC)},
        id=role_id,
    )
    refreshed = await crud_roles.get(db=db, id=role_id, schema_to_select=RoleRead)
    if refreshed is None:
        raise NotFoundException("Role not found.")
    return refreshed


async def delete_role(role_id: int, db: AsyncSession) -> dict[str, str]:
    await get_role(role_id, db)
    stmt = select(func.count()).select_from(AdminUser).where(AdminUser.role_id == role_id, AdminUser.is_deleted.is_(False))
    assigned_count = await db.scalar(stmt)
    if assigned_count and assigned_count > 0:
        raise DuplicateValueException("Role is still assigned to admin accounts.")
    await crud_roles.delete(db=db, id=role_id)
    return {"message": "Role deleted."}
