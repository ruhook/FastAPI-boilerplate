import asyncio
import functools
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, cast

import redis.asyncio as redis

from ..core.config import settings

logger = logging.getLogger(__name__)

DEAD_LETTER_LUA = """
redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[1], '*',
  'original_stream', KEYS[1],
  'original_message_id', ARGV[2],
  'event_id', ARGV[3],
  'event_type', ARGV[4],
  'raw_payload', ARGV[5],
  'delivery_count', ARGV[6],
  'failure_category', ARGV[7],
  'error', ARGV[8],
  'first_seen_at', ARGV[9],
  'dead_lettered_at', ARGV[10])
return redis.call('XACK', KEYS[1], ARGV[11], ARGV[2])
"""


@dataclass(slots=True)
class Message:
    id: str
    data: dict[str, Any] | None
    redis_client: redis.Redis
    raw_payload: str = ""
    decode_error: str | None = None


class QueueType(StrEnum):
    MISC = "misc"


class MalformedEventMessageError(ValueError):
    """Raised when a stream entry cannot be decoded into an event object."""


class AsyncMQClient:
    def __init__(self, queue_type: QueueType, group: str = "") -> None:
        self._queue = f"{settings.EVENT_QUEUE_PREFIX}{queue_type.value}"
        self._dead_letter_queue = f"{self._queue}:dead-letter"
        self._group = group
        self._consumer_id = str(uuid.uuid4())
        self._local_queue: asyncio.Queue[Message] = asyncio.Queue(
            maxsize=settings.EVENT_CONSUMER_BUFFER_SIZE
        )
        self._stop_event = asyncio.Event()
        self._handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._fetcher_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._redis: redis.Redis | None = None
        self._prefer_new = True

    def subscribe(
        self, func: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
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

        self._stop_event.clear()
        self._fetcher_task = asyncio.create_task(self._fetcher(), name=f"{self._queue}-fetcher")
        self._worker_tasks = [
            asyncio.create_task(self._worker(index), name=f"{self._queue}-worker-{index}")
            for index in range(settings.EVENT_CONSUMER_CONCURRENCY)
        ]
        try:
            await self._stop_event.wait()
        finally:
            await self._cleanup()

    async def _fetcher(self) -> None:
        redis_client = await self._get_redis()
        while not self._stop_event.is_set():
            try:
                message = await self._get_item(redis_client, block_time=1000)
                if message is not None:
                    await self._local_queue.put(message)
            except redis.ConnectionError:
                logger.warning("Event stream connection unavailable", extra={"stream": self._queue})
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Event stream fetch failed",
                    extra={"stream": self._queue, "error_type": type(exc).__name__},
                )
                await asyncio.sleep(1)

    async def _worker(self, worker_id: int) -> None:
        while True:
            message = await self._local_queue.get()
            try:
                await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    await self._handle_failure(message, exc, worker_id=worker_id)
                except asyncio.CancelledError:
                    raise
                except Exception as failure_exc:
                    logger.error(
                        "Failed to record event delivery failure; message remains pending",
                        extra={
                            "stream": self._queue,
                            "message_id": message.id,
                            "worker_id": worker_id,
                            "error_type": type(failure_exc).__name__,
                        },
                    )
            finally:
                self._local_queue.task_done()

    async def _get_item(self, redis_client: redis.Redis, block_time: int = 1000) -> Message | None:
        if self._prefer_new:
            message = await self._read_new_item(redis_client, block_time=block_time)
            if message is None:
                message = await self._claim_stale_item(redis_client)
        else:
            message = await self._claim_stale_item(redis_client)
            if message is None:
                message = await self._read_new_item(redis_client, block_time=block_time)
        self._prefer_new = not self._prefer_new
        return message

    async def _read_new_item(self, redis_client: redis.Redis, *, block_time: int) -> Message | None:
        received = await redis_client.xreadgroup(
            self._group,
            self._consumer_id,
            {self._queue: ">"},
            count=1,
            block=block_time,
        )
        if not received:
            return None
        _, messages = received[0]
        return self._decode_message(messages[0], redis_client)

    async def _claim_stale_item(self, redis_client: redis.Redis) -> Message | None:
        try:
            claimed = await redis_client.xautoclaim(
                self._queue,
                self._group,
                self._consumer_id,
                min_idle_time=settings.EVENT_PENDING_IDLE_MS,
                start_id="0-0",
                count=1,
            )
        except redis.ResponseError:
            return None

        messages = claimed[1] if len(claimed) > 1 else []
        if not messages:
            return None
        return self._decode_message(messages[0], redis_client)

    @staticmethod
    def _decode_message(raw_message: tuple[str, dict[str, Any]], redis_client: redis.Redis) -> Message:
        message_id, fields = raw_message
        payload_value = fields.get("json", "")
        raw_payload = payload_value if isinstance(payload_value, str) else str(payload_value)
        try:
            decoded = json.loads(raw_payload)
        except json.JSONDecodeError:
            return Message(
                id=message_id,
                data=None,
                redis_client=redis_client,
                raw_payload=raw_payload,
                decode_error="JSONDecodeError",
            )
        if not isinstance(decoded, dict):
            return Message(
                id=message_id,
                data=None,
                redis_client=redis_client,
                raw_payload=raw_payload,
                decode_error="NonObjectPayload",
            )
        return Message(
            id=message_id,
            data=decoded,
            redis_client=redis_client,
            raw_payload=raw_payload,
        )

    async def _ack(self, message: Message) -> None:
        await message.redis_client.xack(self._queue, self._group, message.id)

    async def _handle_message(self, message: Message) -> None:
        if self._handler is None:
            raise ValueError("Handler function is required. Use @mq.subscribe.")
        if message.data is None or message.decode_error is not None:
            raise MalformedEventMessageError(message.decode_error or "Malformed event message")
        await self._handler(message.data)
        await self._ack(message)

    async def _get_delivery_count(self, message: Message) -> int:
        pending = await message.redis_client.xpending_range(
            self._queue,
            self._group,
            min=message.id,
            max=message.id,
            count=1,
        )
        if not pending:
            return 1
        entry = pending[0]
        if isinstance(entry, dict):
            return max(1, int(entry.get("times_delivered", 1)))
        return max(1, int(getattr(entry, "times_delivered", 1)))

    async def _handle_failure(self, message: Message, exc: Exception, *, worker_id: int = -1) -> None:
        delivery_count = await self._get_delivery_count(message)
        if delivery_count < settings.EVENT_CONSUMER_MAX_DELIVERIES:
            logger.warning(
                "Event handler failed; message left pending",
                extra={
                    "stream": self._queue,
                    "message_id": message.id,
                    "worker_id": worker_id,
                    "delivery_count": delivery_count,
                    "error_type": type(exc).__name__,
                },
            )
            return
        await self._move_to_dead_letter(message, exc, delivery_count=delivery_count)

    async def _move_to_dead_letter(self, message: Message, exc: Exception, *, delivery_count: int) -> None:
        data = message.data or {}
        raw_payload = (message.raw_payload or json.dumps(data, ensure_ascii=False))[
            : settings.EVENT_DEAD_LETTER_RAW_MAX_CHARS
        ]
        error_summary = type(exc).__name__[: settings.EVENT_DEAD_LETTER_ERROR_MAX_CHARS]
        failure_category = "malformed" if message.decode_error else type(exc).__name__
        await cast(
            Awaitable[Any],
            message.redis_client.eval(
                DEAD_LETTER_LUA,
                2,
                self._queue,
                self._dead_letter_queue,
                str(settings.EVENT_DEAD_LETTER_MAXLEN),
                message.id,
                str(data.get("event_id") or ""),
                str(data.get("type") or ""),
                raw_payload,
                str(delivery_count),
                failure_category,
                error_summary,
                self._first_seen_at(message.id),
                datetime.now(UTC).isoformat(),
                self._group,
            ),
        )
        logger.error(
            "Event moved to dead-letter stream",
            extra={
                "stream": self._queue,
                "dead_letter_stream": self._dead_letter_queue,
                "message_id": message.id,
                "event_id": data.get("event_id"),
                "delivery_count": delivery_count,
                "failure_category": failure_category,
            },
        )

    @staticmethod
    def _first_seen_at(message_id: str) -> str:
        try:
            milliseconds = int(message_id.split("-", 1)[0])
        except (TypeError, ValueError):
            return ""
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()

    async def _cleanup(self) -> None:
        if self._fetcher_task is not None:
            self._fetcher_task.cancel()
            await asyncio.gather(self._fetcher_task, return_exceptions=True)
            self._fetcher_task = None

        try:
            await asyncio.wait_for(
                self._local_queue.join(),
                timeout=settings.EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Event worker shutdown timed out; unfinished messages remain pending",
                extra={"stream": self._queue, "queued_messages": self._local_queue.qsize()},
            )

        for worker in self._worker_tasks:
            worker.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[attr-defined]
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
            await self._redis.aclose()  # type: ignore[attr-defined]
            self._redis = None
