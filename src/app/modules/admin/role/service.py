from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from ..admin_user.model import AdminUser
from .const import ALL_ADMIN_PERMISSIONS, deduplicate_permissions
from .crud import crud_roles
from .model import Role
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


def sanitize_role_permissions(permissions: list[str]) -> list[str]:
    return deduplicate_permissions([permission for permission in permissions if permission in ALL_ADMIN_PERMISSIONS])


def serialize_role(role: Role) -> dict[str, Any]:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        enabled=role.enabled,
        permissions=sanitize_role_permissions(role.permissions or []),
        created_at=role.created_at,
        updated_at=role.updated_at,
        data=role.data or {},
    ).model_dump()


async def list_roles(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(Role).order_by(Role.created_at.desc()))
    roles = result.scalars().all()
    return [serialize_role(role) for role in roles]


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
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFoundException("Role not found.")
    return serialize_role(role)


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
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFoundException("Role not found.")
    await db.execute(
        update(AdminUser)
        .where(AdminUser.role_id == role_id)
        .values(role_id=None, updated_at=datetime.now(UTC))
    )
    await db.flush()
    await crud_roles.delete(db=db, id=role_id)
    return {"message": "Role deleted."}
