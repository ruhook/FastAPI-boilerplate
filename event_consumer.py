"""
独立事件消费者进程
启动方式: python event_consumer.py
"""

import asyncio

from src.app.core.config import settings
from src.app.core.logger import init_logging
from src.app.event import AsyncEventManager, AsyncMQClient, EventType, QueueType
from src.app.event.handlers.example import handle_example_event
from src.app.event.handlers.mail import handle_mail_task_created


init_logging(service_name="event")

GROUP = settings.EVENT_CONSUMER_GROUP
mq = AsyncMQClient(QueueType.MISC, group=GROUP)
event_manager = AsyncEventManager(
    concurrency=settings.EVENT_CONSUMER_CONCURRENCY,
    stats_interval=settings.EVENT_STATS_INTERVAL,
)

event_manager.register_handler(EventType.EXAMPLE, handle_example_event)
event_manager.register_handler(EventType.MAIL_TASK_CREATED, handle_mail_task_created)
event_manager.set_mq_client(mq)


@mq.subscribe
async def dispatch_event(msg: dict) -> None:
    await event_manager.receive(msg)


if __name__ == "__main__":
    try:
        asyncio.run(event_manager.run())
    except KeyboardInterrupt:
        pass
