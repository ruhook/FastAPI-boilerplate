from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import require_admin_permission, require_any_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.form_template.schema import FormTemplateCreate, FormTemplateRead, FormTemplateUpdate
from .....modules.admin.form_template.service import (
    create_form_template,
    delete_form_template,
    get_form_template,
    list_form_templates,
    update_form_template,
)

router = APIRouter(prefix="/form-templates", tags=["admin-form-templates"])


@router.get(
    "",
    response_model=list[FormTemplateRead],
    dependencies=[Depends(require_any_admin_permission("岗位管理", "报名表单策略"))],
)
async def read_form_templates(db: Annotated[AsyncSession, Depends(async_get_db)]) -> list[dict[str, Any]]:
    return await list_form_templates(db)


@router.post(
    "",
    response_model=FormTemplateRead,
    status_code=201,
    dependencies=[Depends(require_admin_permission("报名表单策略"))],
)
async def create_form_template_endpoint(
    payload: FormTemplateCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await create_form_template(payload, db)


@router.get(
    "/{template_id}",
    response_model=FormTemplateRead,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "报名表单策略"))],
)
async def read_form_template(template_id: int, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    return await get_form_template(template_id, db)


@router.patch(
    "/{template_id}",
    response_model=FormTemplateRead,
    dependencies=[Depends(require_admin_permission("报名表单策略"))],
)
async def update_form_template_endpoint(
    template_id: int,
    payload: FormTemplateUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await update_form_template(template_id, payload, db)


@router.delete("/{template_id}", dependencies=[Depends(require_admin_permission("报名表单策略"))])
async def delete_form_template_endpoint(
    template_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    return await delete_form_template(template_id, db)
