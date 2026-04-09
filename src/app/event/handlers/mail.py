import logging
from typing import Any

from ...core.exceptions.http_exceptions import NotFoundException
from ...modules.admin.mail_task.service import process_mail_task


logger = logging.getLogger(__name__)


async def handle_mail_task_created(msg: dict[str, Any]) -> None:
    mail_task_id = msg.get("mail_task_id")
    if not isinstance(mail_task_id, int):
        logger.warning("MAIL_TASK_CREATED missing mail_task_id", extra={"event_message": msg})
        return

    logger.info("Processing MAIL_TASK_CREATED", extra={"mail_task_id": mail_task_id})
    try:
        await process_mail_task(mail_task_id)
    except NotFoundException:
        logger.warning("MAIL_TASK_CREATED target task not found, skipping stale event", extra={"mail_task_id": mail_task_id})
