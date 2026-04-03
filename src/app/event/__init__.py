import time
from enum import StrEnum
from typing import Any

from .event_manager import AsyncEventManager
from .mq_client import AsyncMQClient, QueueType


class EventType(StrEnum):
    EXAMPLE = "example"
    USER_REGISTERED = "user_registered"
    ADMIN_ACCOUNT_CREATED = "admin_account_created"


async def send_event(ev_type: EventType, data: dict[str, Any], queue_type: QueueType = QueueType.MISC) -> None:
    payload = dict(data)
    payload.setdefault("create_time", int(time.time() * 1000))
    mq = AsyncMQClient(queue_type)
    await mq.put({"type": ev_type, **payload})


__all__ = [
    "AsyncMQClient",
    "AsyncEventManager",
    "QueueType",
    "EventType",
    "send_event",
]
