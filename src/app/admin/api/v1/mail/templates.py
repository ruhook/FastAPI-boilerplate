from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_template.schema import MailTemplateCreate, MailTemplateRead, MailTemplateUpdate
from .....modules.admin.mail_template.service import (
    create_mail_template,
    delete_mail_template,
    get_mail_template,
    list_mail_templates,
    update_mail_template,
)

router = APIRouter()


@router.get("/templates", response_model=list[MailTemplateRead], dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_templates(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> list[dict[str, Any]]:
    return await list_mail_templates(db, admin_user_id=int(current_admin["id"]))


@router.post("/templates", response_model=MailTemplateRead, status_code=201, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def create_mail_template_endpoint(
    payload: MailTemplateCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_mail_template(payload, db, admin_user_id=int(current_admin["id"]))


@router.get("/templates/{template_id}", response_model=MailTemplateRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_template(
    template_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await get_mail_template(template_id, db, admin_user_id=int(current_admin["id"]))


@router.patch("/templates/{template_id}", response_model=MailTemplateRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def update_mail_template_endpoint(
    template_id: int,
    payload: MailTemplateUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_mail_template(template_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete("/templates/{template_id}", dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def delete_mail_template_endpoint(
    template_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, str]:
    return await delete_mail_template(template_id, db, admin_user_id=int(current_admin["id"]))
