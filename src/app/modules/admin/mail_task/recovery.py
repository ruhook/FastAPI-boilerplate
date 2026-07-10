import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.db.database import local_session
from ....event import EventType
from ....event.outbox import enqueue_event
from .const import MailTaskStatus
from .model import MailTask
from .service import (
    clear_mail_task_processing_lease,
    get_mail_delivery_mode,
    resolve_stale_mail_task_recovery,
)

logger = logging.getLogger(__name__)


async def recover_stale_mail_tasks(
    db: AsyncSession,
    *,
    now: datetime,
    delivery_mode: str,
    limit: int,
) -> int:
    result = await db.execute(
        select(MailTask)
        .where(
            MailTask.status.in_([MailTaskStatus.RENDERING.value, MailTaskStatus.SENDING.value]),
            MailTask.processing_lease_expires_at.is_not(None),
            MailTask.processing_lease_expires_at <= now,
        )
        .order_by(MailTask.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    tasks = list(result.scalars().all())
    for task in tasks:
        previous_status = task.status
        next_status, should_enqueue = resolve_stale_mail_task_recovery(
            current_status=previous_status,
            delivery_mode=delivery_mode,
        )
        task.status = next_status
        task.error_message = "Mail worker exited before delivery state was finalized."
        task.updated_at = now
        clear_mail_task_processing_lease(task)
        if should_enqueue:
            await enqueue_event(db, EventType.MAIL_TASK_CREATED, {"mail_task_id": task.id})
        logger.warning(
            "Recovered stale mail task",
            extra={
                "mail_task_id": task.id,
                "previous_status": previous_status,
                "recovered_status": next_status,
            },
        )
    await db.flush()
    return len(tasks)


class MailTaskRecoveryWorker:
    def __init__(self) -> None:
        self._stop_event = asyncio.Event()

    async def recover_once(self) -> int:
        async with local_session() as db:
            recovered = await recover_stale_mail_tasks(
                db,
                now=datetime.now(UTC),
                delivery_mode=get_mail_delivery_mode(),
                limit=settings.MAIL_TASK_RECOVERY_BATCH_SIZE,
            )
            await db.commit()
            return recovered

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                recovered = await self.recover_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Mail task recovery iteration failed")
                recovered = 0

            if recovered:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=settings.MAIL_TASK_RECOVERY_INTERVAL_SECONDS,
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop_event.set()
