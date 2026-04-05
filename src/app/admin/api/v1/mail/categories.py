from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_template_category.schema import (
    MailTemplateCategoryCreate,
    MailTemplateCategoryRead,
    MailTemplateCategoryUpdate,
)
from .....modules.admin.mail_template_category.service import (
    create_mail_template_category,
    delete_mail_template_category,
    list_mail_template_categories,
    update_mail_template_category,
)

router = APIRouter()


@router.post("/template-categories", response_model=MailTemplateCategoryRead, status_code=201, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def create_mail_template_category_endpoint(
    payload: MailTemplateCategoryCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_mail_template_category(payload, db, admin_user_id=int(current_admin["id"]))


@router.get("/template-categories", response_model=list[MailTemplateCategoryRead], dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_template_categories(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> list[dict[str, Any]]:
    return await list_mail_template_categories(db, admin_user_id=int(current_admin["id"]))


@router.patch("/template-categories/{category_id}", response_model=MailTemplateCategoryRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def update_mail_template_category_endpoint(
    category_id: int,
    payload: MailTemplateCategoryUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_mail_template_category(category_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete("/template-categories/{category_id}", dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def delete_mail_template_category_endpoint(
    category_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, str]:
    return await delete_mail_template_category(category_id, db, admin_user_id=int(current_admin["id"]))
