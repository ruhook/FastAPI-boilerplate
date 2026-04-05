from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_signature.schema import MailSignatureCreate, MailSignatureRead, MailSignatureUpdate
from .....modules.admin.mail_signature.service import (
    create_mail_signature,
    delete_mail_signature,
    get_mail_signature,
    list_mail_signatures,
    update_mail_signature,
)

router = APIRouter()


@router.get("/signatures", response_model=list[MailSignatureRead], dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_signatures(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> list[dict[str, Any]]:
    return await list_mail_signatures(db, admin_user_id=int(current_admin["id"]))


@router.post("/signatures", response_model=MailSignatureRead, status_code=201, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def create_mail_signature_endpoint(
    payload: MailSignatureCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_mail_signature(payload, db, admin_user_id=int(current_admin["id"]))


@router.get("/signatures/{signature_id}", response_model=MailSignatureRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_signature(
    signature_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await get_mail_signature(signature_id, db, admin_user_id=int(current_admin["id"]))


@router.patch("/signatures/{signature_id}", response_model=MailSignatureRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def update_mail_signature_endpoint(
    signature_id: int,
    payload: MailSignatureUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_mail_signature(signature_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete("/signatures/{signature_id}", dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def delete_mail_signature_endpoint(
    signature_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, str]:
    return await delete_mail_signature(signature_id, db, admin_user_id=int(current_admin["id"]))
