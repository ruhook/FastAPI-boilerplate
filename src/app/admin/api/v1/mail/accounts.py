from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_account.schema import MailAccountCreate, MailAccountRead, MailAccountUpdate
from .....modules.admin.mail_account.service import (
    create_mail_account,
    delete_mail_account,
    get_mail_account,
    list_mail_accounts,
    update_mail_account,
)

router = APIRouter(prefix="/accounts")


@router.get("", response_model=list[MailAccountRead], dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_accounts(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> list[dict[str, Any]]:
    return await list_mail_accounts(db, admin_user_id=int(current_admin["id"]))


@router.post("", response_model=MailAccountRead, status_code=201, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def create_mail_account_endpoint(
    payload: MailAccountCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_mail_account(payload, db, admin_user_id=int(current_admin["id"]))


@router.get("/{account_id}", response_model=MailAccountRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_account(
    account_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await get_mail_account(account_id, db, admin_user_id=int(current_admin["id"]))


@router.patch("/{account_id}", response_model=MailAccountRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def update_mail_account_endpoint(
    account_id: int,
    payload: MailAccountUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_mail_account(account_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete("/{account_id}", dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def delete_mail_account_endpoint(
    account_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, str]:
    return await delete_mail_account(account_id, db, admin_user_id=int(current_admin["id"]))
