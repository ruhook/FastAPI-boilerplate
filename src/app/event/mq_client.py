import asyncio
import functools
import json
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

import redis.asyncio as redis

from ..core.config import settings


@dataclass(slots=True)
class Message:
    id: str
    data: dict[str, Any]
    redis_client: redis.Redis


class QueueType(StrEnum):
    MISC = "misc"


class AsyncMQClient:
    buffer_length = 1

    def __init__(self, queue_type: QueueType, group: str = "") -> None:
        self._queue = f"{settings.EVENT_QUEUE_PREFIX}{queue_type.value}"
        self._group = group
        self._consumer_id = str(uuid.uuid4())
        self._local_queue: asyncio.Queue[Message] = asyncio.Queue(self.buffer_length)
        self._stop_event = asyncio.Event()
        self._handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._fetcher_task: asyncio.Task[None] | None = None
        self._redis: redis.Redis | None = None

    def subscribe(self, func: Callable[[dict[str, Any]], Awaitable[None]]) -> Callable[[dict[str, Any]], Awaitable[None]]:
        self._handler = func

        @functools.wraps(func)
        async def subscribed_func(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        return subscribed_func

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
        return self._redis

    async def start(self, from_id: str = "0") -> None:
        if not self._group:
            raise ValueError("Consumer group name is required.")
        if self._handler is None:
            raise ValueError("Handler function is required. Use @mq.subscribe.")

        redis_client = await self._get_redis()

        try:
            await redis_client.xgroup_create(self._queue, self._group, mkstream=True, id=from_id)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        self._fetcher_task = asyncio.create_task(self._fetcher())

        while not self._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(self._local_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self._handler(msg.data)
            finally:
                await self._ack(msg)

        await self._cleanup()

    async def _fetcher(self) -> None:
        redis_client = await self._get_redis()

        while not self._stop_event.is_set():
            try:
                msg = await self._get_item(redis_client, block_time=1000)
                if msg is None:
                    continue
                await self._local_queue.put(msg)
            except redis.ConnectionError:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    async def _get_item(self, redis_client: redis.Redis, block_time: int = 1000) -> Message | None:
        recv_data = await redis_client.xreadgroup(
            self._group,
            self._consumer_id,
            {self._queue: ">"},
            count=1,
            block=block_time,
        )

        if not recv_data:
            return None

        _, messages = recv_data[0]
        msg_id, msg = messages[0]
        payload = msg.get("json", "{}")
        return Message(
            id=msg_id,
            data=json.loads(payload),
            redis_client=redis_client,
        )

    async def _ack(self, msg: Message) -> None:
        await msg.redis_client.xack(self._queue, self._group, msg.id)

    async def _cleanup(self) -> None:
        if self._fetcher_task:
            self._fetcher_task.cancel()
            try:
                await self._fetcher_task
            except asyncio.CancelledError:
                pass

        while not self._local_queue.empty():
            try:
                msg = self._local_queue.get_nowait()
                await self.put(msg.data)
            except asyncio.QueueEmpty:
                break

        if self._redis is not None:
            try:
                await self._redis.xgroup_delconsumer(self._queue, self._group, self._consumer_id)
            except Exception:
                pass
            await self._redis.aclose()
            self._redis = None

    async def stop(self) -> None:
        self._stop_event.set()

    async def put(self, msg: dict[str, Any]) -> None:
        redis_client = await self._get_redis()

        encoded_msg = deepcopy(msg)
        if "type" in encoded_msg and isinstance(encoded_msg["type"], Enum):
            encoded_msg["type"] = encoded_msg["type"].value

        await redis_client.xadd(self._queue, {"json": json.dumps(encoded_msg, ensure_ascii=False)})

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
