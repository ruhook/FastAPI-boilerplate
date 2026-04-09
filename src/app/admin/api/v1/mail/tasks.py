from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user, require_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.mail_task.schema import MailTaskRead
from .....modules.admin.mail_task.service import list_mail_tasks, resend_mail_task

router = APIRouter(prefix="/tasks")


@router.get("", response_model=list[MailTaskRead], dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_tasks(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> list[dict[str, Any]]:
    return await list_mail_tasks(db, admin_user_id=int(current_admin["id"]))


@router.post("/{task_id}/resend", response_model=MailTaskRead, dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def resend_mail_task_endpoint(
    task_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await resend_mail_task(task_id, db, admin_user_id=int(current_admin["id"]))
