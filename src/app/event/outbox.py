from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..modules.event_outbox.model import EventOutbox
from . import EventType


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"


async def enqueue_event(
    db: AsyncSession,
    event_type: EventType | str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    available_at: datetime | None = None,
    max_attempts: int = 8,
) -> EventOutbox:
    now = datetime.now(UTC)
    row = EventOutbox(
        event_id=event_id or str(uuid4()),
        event_type=event_type.value if isinstance(event_type, EventType) else str(event_type),
        payload=dict(payload),
        status=OutboxStatus.PENDING.value,
        available_at=available_at or now,
        attempt_count=0,
        max_attempts=max_attempts,
        lease_owner=None,
        lease_expires_at=None,
        created_at=now,
        published_at=None,
        processed_at=None,
        failed_at=None,
        last_error=None,
    )
    db.add(row)
    await db.flush()
    return row


def mark_outbox_published(row: EventOutbox, *, now: datetime | None = None) -> None:
    timestamp = now or datetime.now(UTC)
    row.status = OutboxStatus.PUBLISHED.value
    row.published_at = timestamp
    row.lease_owner = None
    row.lease_expires_at = None
    row.last_error = None


def mark_outbox_publish_failed(
    row: EventOutbox,
    error: Exception,
    *,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.now(UTC)
    row.attempt_count += 1
    row.lease_owner = None
    row.lease_expires_at = None
    row.last_error = type(error).__name__[:255]
    if row.attempt_count >= row.max_attempts:
        row.status = OutboxStatus.FAILED.value
        row.failed_at = timestamp
        return

    backoff_seconds = min(300, 5 * (2 ** (row.attempt_count - 1)))
    row.status = OutboxStatus.PENDING.value
    row.available_at = timestamp + timedelta(seconds=backoff_seconds)
