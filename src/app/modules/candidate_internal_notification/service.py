from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import NotFoundException
from .model import CandidateInternalNotification
from .schema import (
    CandidateInternalNotificationListPage,
    CandidateInternalNotificationRead,
    CandidateInternalNotificationReadAllResponse,
)


def serialize_candidate_internal_notification(notification: CandidateInternalNotification) -> dict[str, Any]:
    return CandidateInternalNotificationRead(
        id=notification.id,
        recipient_user_id=notification.recipient_user_id,
        sender_admin_user_id=notification.sender_admin_user_id,
        category=notification.category,
        title=notification.title,
        description=notification.description,
        action_url=notification.action_url,
        is_read=notification.is_read,
        read_at=notification.read_at,
        created_at=notification.created_at,
        updated_at=notification.updated_at,
        data=notification.data or {},
    ).model_dump()


async def create_candidate_internal_notification(
    *,
    db: AsyncSession,
    recipient_user_id: int,
    category: str,
    title: str,
    description: str,
    action_url: str | None = None,
    sender_admin_user_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    notification = CandidateInternalNotification(
        recipient_user_id=recipient_user_id,
        sender_admin_user_id=sender_admin_user_id,
        category=category,
        title=title,
        description=description,
        action_url=action_url,
        is_read=False,
        data=data or {},
    )
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return serialize_candidate_internal_notification(notification)


async def list_candidate_internal_notifications(
    *,
    db: AsyncSession,
    recipient_user_id: int,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
) -> dict[str, Any]:
    normalized_page = max(page, 1)
    normalized_page_size = min(max(page_size, 1), 100)

    base_filters = [CandidateInternalNotification.recipient_user_id == recipient_user_id]
    unread_filter = [CandidateInternalNotification.is_read.is_(False)]

    count_query = select(func.count()).select_from(CandidateInternalNotification).where(*base_filters)
    if unread_only:
        count_query = count_query.where(*unread_filter)
    total = int((await db.execute(count_query)).scalar_one() or 0)

    unread_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(CandidateInternalNotification)
                .where(*base_filters, *unread_filter)
            )
        ).scalar_one()
        or 0
    )

    query = (
        select(CandidateInternalNotification)
        .where(*base_filters)
        .order_by(
            CandidateInternalNotification.is_read.asc(),
            CandidateInternalNotification.created_at.desc(),
            CandidateInternalNotification.id.desc(),
        )
        .offset((normalized_page - 1) * normalized_page_size)
        .limit(normalized_page_size)
    )
    if unread_only:
        query = query.where(*unread_filter)

    items = (await db.execute(query)).scalars().all()
    return CandidateInternalNotificationListPage(
        items=[CandidateInternalNotificationRead(**serialize_candidate_internal_notification(item)) for item in items],
        total=total,
        unread_count=unread_count,
        page=normalized_page,
        page_size=normalized_page_size,
    ).model_dump()


async def mark_candidate_internal_notification_read(
    *,
    notification_id: int,
    db: AsyncSession,
    recipient_user_id: int,
) -> dict[str, Any]:
    result = await db.execute(
        select(CandidateInternalNotification).where(
            CandidateInternalNotification.id == notification_id,
            CandidateInternalNotification.recipient_user_id == recipient_user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise NotFoundException("Internal notification not found.")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(UTC)
        notification.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(notification)

    return serialize_candidate_internal_notification(notification)


async def mark_all_candidate_internal_notifications_read(
    *,
    db: AsyncSession,
    recipient_user_id: int,
) -> dict[str, Any]:
    result = await db.execute(
        select(CandidateInternalNotification).where(
            CandidateInternalNotification.recipient_user_id == recipient_user_id,
            CandidateInternalNotification.is_read.is_(False),
        )
    )
    items = result.scalars().all()
    now = datetime.now(UTC)
    for item in items:
        item.is_read = True
        item.read_at = now
        item.updated_at = now

    if items:
        await db.flush()

    return CandidateInternalNotificationReadAllResponse(updated_count=len(items)).model_dump()
