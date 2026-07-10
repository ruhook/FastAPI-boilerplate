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
from src.app.event.outbox_dispatcher import OutboxDispatcher

init_logging(service_name="event")

GROUP = settings.EVENT_CONSUMER_GROUP
mq = AsyncMQClient(QueueType.MISC, group=GROUP)
event_manager = AsyncEventManager(
    stats_interval=settings.EVENT_STATS_INTERVAL,
)

event_manager.register_handler(EventType.EXAMPLE, handle_example_event)
event_manager.register_handler(EventType.MAIL_TASK_CREATED, handle_mail_task_created)
event_manager.set_mq_client(mq)
outbox_dispatcher = OutboxDispatcher()


@mq.subscribe
async def dispatch_event(msg: dict) -> None:
    await event_manager.receive(msg)


async def run_event_services() -> None:
    dispatcher_task = asyncio.create_task(outbox_dispatcher.run())
    try:
        await event_manager.run()
    finally:
        outbox_dispatcher.stop()
        await dispatcher_task


if __name__ == "__main__":
    try:
        asyncio.run(run_event_services())
    except KeyboardInterrupt:
        pass
