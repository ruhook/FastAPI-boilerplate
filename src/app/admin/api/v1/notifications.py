from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user
from ....core.db.database import async_get_db
from ....modules.admin.internal_notification.schema import (
    AdminInternalNotificationListPage,
    AdminInternalNotificationRead,
    AdminInternalNotificationReadAllResponse,
)
from ....modules.admin.internal_notification.service import (
    list_admin_internal_notifications,
    mark_admin_internal_notification_read,
    mark_all_admin_internal_notifications_read,
)

router = APIRouter(prefix="/notifications", tags=["admin-notifications"])


@router.get("", response_model=AdminInternalNotificationListPage)
async def read_admin_internal_notifications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
) -> dict[str, Any]:
    return await list_admin_internal_notifications(
        db=db,
        recipient_admin_user_id=int(current_admin["id"]),
        page=page,
        page_size=page_size,
        unread_only=unread_only,
    )


@router.post("/{notification_id}/read", response_model=AdminInternalNotificationRead)
async def read_admin_internal_notification(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await mark_admin_internal_notification_read(
        notification_id=notification_id,
        db=db,
        recipient_admin_user_id=int(current_admin["id"]),
    )


@router.post("/read-all", response_model=AdminInternalNotificationReadAllResponse)
async def read_all_admin_internal_notifications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await mark_all_admin_internal_notifications_read(
        db=db,
        recipient_admin_user_id=int(current_admin["id"]),
    )
