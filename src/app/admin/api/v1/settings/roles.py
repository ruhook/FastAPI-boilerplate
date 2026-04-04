from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.role.schema import RoleCreate, RoleRead, RoleUpdate
from .....modules.admin.role.service import create_role, delete_role, get_role, list_roles, update_role

router = APIRouter(prefix="/roles", tags=["admin-roles"])


@router.get("", response_model=list[RoleRead], dependencies=[Depends(require_admin_permission("权限与角色"))])
async def read_roles(db: Annotated[AsyncSession, Depends(async_get_db)]) -> list[dict[str, Any]]:
    return await list_roles(db)


@router.post("", response_model=RoleRead, status_code=201, dependencies=[Depends(require_admin_permission("权限与角色"))])
async def create_role_endpoint(payload: RoleCreate, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    return await create_role(payload, db)


@router.get("/{role_id}", response_model=RoleRead, dependencies=[Depends(require_admin_permission("权限与角色"))])
async def read_role(role_id: int, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    return await get_role(role_id, db)


@router.patch("/{role_id}", response_model=RoleRead, dependencies=[Depends(require_admin_permission("权限与角色"))])
async def update_role_endpoint(
    role_id: int,
    payload: RoleUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await update_role(role_id, payload, db)


@router.delete("/{role_id}", dependencies=[Depends(require_admin_permission("权限与角色"))])
async def delete_role_endpoint(role_id: int, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, str]:
    return await delete_role(role_id, db)
