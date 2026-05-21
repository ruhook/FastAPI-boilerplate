from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...modules.candidate_internal_notification.schema import (
    CandidateInternalNotificationListPage,
    CandidateInternalNotificationRead,
    CandidateInternalNotificationReadAllResponse,
)
from ...modules.candidate_internal_notification.service import (
    list_candidate_internal_notifications,
    mark_all_candidate_internal_notifications_read,
    mark_candidate_internal_notification_read,
)
from ..dependencies import get_current_user

router = APIRouter(prefix="/notifications", tags=["web-notifications"])


@router.get("", response_model=CandidateInternalNotificationListPage)
async def read_candidate_internal_notifications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
) -> dict[str, Any]:
    return await list_candidate_internal_notifications(
        db=db,
        recipient_user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        unread_only=unread_only,
    )


@router.post("/{notification_id}/read", response_model=CandidateInternalNotificationRead)
async def read_candidate_internal_notification(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return await mark_candidate_internal_notification_read(
        notification_id=notification_id,
        db=db,
        recipient_user_id=int(current_user["id"]),
    )


@router.post("/read-all", response_model=CandidateInternalNotificationReadAllResponse)
async def read_all_candidate_internal_notifications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return await mark_all_candidate_internal_notifications_read(
        db=db,
        recipient_user_id=int(current_user["id"]),
    )
