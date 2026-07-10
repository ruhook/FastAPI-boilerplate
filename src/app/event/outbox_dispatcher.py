import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.db.database import local_session
from ..modules.event_outbox.model import EventOutbox
from . import EventType, send_event
from .outbox import OutboxStatus, mark_outbox_publish_failed, mark_outbox_published

logger = logging.getLogger(__name__)


async def claim_outbox_batch(
    db: AsyncSession,
    *,
    lease_owner: str,
    limit: int,
    now: datetime | None = None,
) -> list[EventOutbox]:
    timestamp = now or datetime.now(UTC)
    result = await db.execute(
        select(EventOutbox)
        .where(
            or_(
                and_(
                    EventOutbox.status == OutboxStatus.PENDING.value,
                    EventOutbox.available_at <= timestamp,
                ),
                and_(
                    EventOutbox.status == OutboxStatus.PROCESSING.value,
                    EventOutbox.lease_expires_at <= timestamp,
                ),
            )
        )
        .order_by(EventOutbox.available_at.asc(), EventOutbox.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    lease_expires_at = timestamp + timedelta(seconds=settings.EVENT_OUTBOX_LEASE_SECONDS)
    for row in rows:
        row.status = OutboxStatus.PROCESSING.value
        row.lease_owner = lease_owner
        row.lease_expires_at = lease_expires_at
    await db.flush()
    return rows


def build_outbox_event_message(row: EventOutbox) -> dict[str, object]:
    return {**row.payload, "event_id": row.event_id}


class OutboxDispatcher:
    def __init__(self) -> None:
        self.lease_owner = str(uuid4())
        self._stop_event = asyncio.Event()

    async def _publish(self, row: EventOutbox) -> None:
        await send_event(EventType(row.event_type), build_outbox_event_message(row))

    async def dispatch_once(self) -> int:
        async with local_session() as db:
            rows = await claim_outbox_batch(
                db,
                lease_owner=self.lease_owner,
                limit=settings.EVENT_OUTBOX_BATCH_SIZE,
            )
            await db.commit()

        for claimed in rows:
            try:
                await self._publish(claimed)
                error: Exception | None = None
            except Exception as exc:
                error = exc
                logger.exception(
                    "Failed to publish outbox event",
                    extra={"event_id": claimed.event_id, "event_type": claimed.event_type},
                )

            async with local_session() as db:
                result = await db.execute(select(EventOutbox).where(EventOutbox.id == claimed.id).with_for_update())
                current = result.scalar_one_or_none()
                if current is None or current.lease_owner != self.lease_owner:
                    await db.rollback()
                    continue
                if error is None:
                    mark_outbox_published(current)
                else:
                    mark_outbox_publish_failed(current, error)
                await db.commit()

        return len(rows)

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox dispatcher iteration failed")
                processed = 0
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=settings.EVENT_OUTBOX_POLL_SECONDS,
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop_event.set()
