from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission, require_any_admin_permission
from .....core.db.database import async_get_db
from .....core.exceptions.http_exceptions import NotFoundException
from .....modules.admin.admin_user.schema import AdminUserCreate, AdminUserCreateResponse, AdminUserRead, AdminUserUpdate
from .....modules.admin.admin_user.service import (
    create_admin_account as create_admin_account_service,
    delete_admin_account as delete_admin_account_service,
    get_account_with_role,
    query_admin_accounts,
    serialize_admin_user,
    update_admin_account as update_admin_account_service,
)

router = APIRouter(prefix="/accounts", tags=["admin-accounts"])


@router.get("", response_model=list[AdminUserRead], dependencies=[Depends(require_admin_permission("账户管理"))])
async def read_admin_accounts(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    keyword: str | None = None,
) -> list[dict]:
    return await query_admin_accounts(db=db, keyword=keyword)


@router.get(
    "/reviewers",
    response_model=list[AdminUserRead],
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题", "账户管理"))],
)
async def read_assessment_reviewer_accounts(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    keyword: str | None = None,
) -> list[dict]:
    return await query_admin_accounts(db=db, keyword=keyword, required_permission="测试题判题")


@router.post(
    "",
    response_model=AdminUserCreateResponse,
    status_code=201,
    dependencies=[Depends(require_admin_permission("账户管理"))],
)
async def create_admin_account(
    payload: AdminUserCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    return await create_admin_account_service(payload=payload, db=db)


@router.get("/{account_id}", response_model=AdminUserRead, dependencies=[Depends(require_admin_permission("账户管理"))])
async def read_admin_account(
    account_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    account_with_role = await get_account_with_role(db, account_id)
    if account_with_role is None:
        raise NotFoundException("Admin account not found.")
    account, role_name, effective_role_id = account_with_role
    return serialize_admin_user(account, role_name, effective_role_id)


@router.patch("/{account_id}", response_model=AdminUserRead, dependencies=[Depends(require_admin_permission("账户管理"))])
async def update_admin_account(
    account_id: int,
    payload: AdminUserUpdate,
    current_admin: Annotated[dict, Depends(get_current_admin_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    return await update_admin_account_service(
        account_id=account_id,
        payload=payload,
        current_admin=current_admin,
        db=db,
    )


@router.delete("/{account_id}", dependencies=[Depends(require_admin_permission("账户管理"))])
async def delete_admin_account(
    account_id: int,
    current_admin: Annotated[dict, Depends(get_current_admin_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    return await delete_admin_account_service(account_id=account_id, current_admin=current_admin, db=db)
