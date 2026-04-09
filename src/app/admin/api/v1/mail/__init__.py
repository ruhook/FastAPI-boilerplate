from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_task.schema import MailTaskCreate, MailTaskRead
from .....modules.admin.mail_task.service import create_mail_task
from .accounts import router as mail_accounts_router
from .categories import router as mail_categories_router
from .signatures import router as mail_signatures_router
from .tasks import router as mail_tasks_router
from .templates import router as mail_templates_router
from .variables import router as mail_variables_router

router = APIRouter(prefix="/mail", tags=["admin-mail"])
router.include_router(mail_accounts_router)
router.include_router(mail_categories_router)
router.include_router(mail_templates_router)
router.include_router(mail_signatures_router)
router.include_router(mail_variables_router)
router.include_router(mail_tasks_router)


@router.post("/send", response_model=MailTaskRead, status_code=201, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def create_mail_task_endpoint(
    payload: MailTaskCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_mail_task(payload, db, admin_user_id=int(current_admin["id"]))


__all__ = ["router"]
