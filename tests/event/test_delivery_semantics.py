import asyncio

import pytest

from src.app.event.event_manager import AsyncEventManager, UnhandledEventError
from src.app.event.mq_client import AsyncMQClient, Message, QueueType

pytestmark = pytest.mark.no_database_cleanup


class FakeRedis:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []

    async def xack(self, queue: str, group: str, message_id: str) -> None:
        self.acked.append((queue, group, message_id))


class FakeClaimRedis:
    def __init__(self) -> None:
        self.claim_calls = 0
        self.read_calls = 0

    async def xautoclaim(
        self, *args: object, **kwargs: object
    ) -> tuple[str, list[tuple[str, dict[str, str]]], list[str]]:
        self.claim_calls += 1
        return (
            "0-0",
            [("9-0", {"json": '{"type":"example","event_id":"stable-id"}'})],
            [],
        )

    async def xreadgroup(self, *args: object, **kwargs: object) -> list[object]:
        self.read_calls += 1
        return []


class FairFakeRedis:
    def __init__(self) -> None:
        self.new_delivered = False
        self.stale_delivered = False

    async def xreadgroup(
        self,
        *args: object,
        **kwargs: object,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        if self.new_delivered:
            return []
        self.new_delivered = True
        return [("stream", [("new-1", {"json": '{"type":"example"}'})])]

    async def xautoclaim(
        self,
        *args: object,
        **kwargs: object,
    ) -> tuple[str, list[tuple[str, dict[str, str]]], list[str]]:
        if self.stale_delivered:
            return ("0-0", [], [])
        self.stale_delivered = True
        return ("0-0", [("stale-1", {"json": '{"type":"example"}'})], [])


class DeadLetterFakeRedis(FakeRedis):
    def __init__(self, delivery_count: int) -> None:
        super().__init__()
        self.delivery_count = delivery_count
        self.dead_letters: list[dict[str, str]] = []

    async def xpending_range(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
        return [{"message_id": "9-0", "times_delivered": self.delivery_count}]

    async def eval(self, _script: str, _numkeys: int, source: str, dlq: str, *args: object) -> int:
        fields = {
            "original_stream": source,
            "dead_letter_stream": dlq,
            "original_message_id": str(args[1]),
            "delivery_count": str(args[5]),
            "first_seen_at": str(args[8]),
            "dead_lettered_at": str(args[9]),
        }
        self.dead_letters.append(fields)
        self.acked.append((source, str(args[10]), str(args[1])))
        return 1


@pytest.mark.asyncio
async def test_mq_acknowledges_only_after_handler_success() -> None:
    redis = FakeRedis()
    message = Message(id="1-0", data={"type": "example"}, redis_client=redis)  # type: ignore[arg-type]
    client = AsyncMQClient(QueueType.MISC, group="test-group")

    async def success(payload: dict[str, object]) -> None:
        return None

    client._handler = success
    await client._handle_message(message)

    assert len(redis.acked) == 1


@pytest.mark.asyncio
async def test_mq_leaves_failed_handler_message_pending() -> None:
    redis = FakeRedis()
    message = Message(id="1-0", data={"type": "example"}, redis_client=redis)  # type: ignore[arg-type]
    client = AsyncMQClient(QueueType.MISC, group="test-group")

    async def fail(payload: dict[str, object]) -> None:
        raise RuntimeError("handler failed")

    client._handler = fail

    with pytest.raises(RuntimeError, match="handler failed"):
        await client._handle_message(message)

    assert redis.acked == []


@pytest.mark.asyncio
async def test_mq_reclaims_stale_pending_message_when_no_new_work_exists() -> None:
    redis = FakeClaimRedis()
    client = AsyncMQClient(QueueType.MISC, group="test-group")

    message = await client._get_item(redis)  # type: ignore[arg-type]

    assert message is not None
    assert message.id == "9-0"
    assert message.data["event_id"] == "stable-id"
    assert redis.claim_calls == 1
    assert redis.read_calls == 1


@pytest.mark.asyncio
async def test_fetch_alternates_new_and_stale_work() -> None:
    redis = FairFakeRedis()
    client = AsyncMQClient(QueueType.MISC, group="test-group")

    first = await client._get_item(redis, block_time=0)  # type: ignore[arg-type]
    second = await client._get_item(redis, block_time=0)  # type: ignore[arg-type]

    assert first is not None and second is not None
    assert [first.id, second.id] == ["new-1", "stale-1"]


def test_malformed_json_keeps_message_identity_for_bounded_retry() -> None:
    redis = FakeRedis()

    message = AsyncMQClient._decode_message(("broken-1", {"json": "{not-json"}), redis)  # type: ignore[arg-type]

    assert message.id == "broken-1"
    assert message.data is None
    assert message.raw_payload == "{not-json"
    assert message.decode_error == "JSONDecodeError"


@pytest.mark.asyncio
async def test_poison_message_moves_to_dlq_at_delivery_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = DeadLetterFakeRedis(delivery_count=3)
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    monkeypatch.setattr("src.app.event.mq_client.settings.EVENT_CONSUMER_MAX_DELIVERIES", 3)
    message = Message(id="9-0", data={"type": "unknown"}, redis_client=redis)  # type: ignore[arg-type]

    await client._handle_failure(message, UnhandledEventError("unknown"))

    assert redis.dead_letters[0]["original_message_id"] == "9-0"
    assert redis.dead_letters[0]["delivery_count"] == "3"
    assert redis.dead_letters[0]["first_seen_at"] == "1970-01-01T00:00:00.009000+00:00"
    assert redis.dead_letters[0]["dead_lettered_at"]
    assert redis.acked == [(client._queue, "test-group", "9-0")]


@pytest.mark.asyncio
async def test_retryable_failure_remains_pending_below_delivery_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = DeadLetterFakeRedis(delivery_count=2)
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    monkeypatch.setattr("src.app.event.mq_client.settings.EVENT_CONSUMER_MAX_DELIVERIES", 3)
    message = Message(id="9-0", data={"type": "example"}, redis_client=redis)  # type: ignore[arg-type]

    await client._handle_failure(message, RuntimeError("retryable"))

    assert redis.dead_letters == []
    assert redis.acked == []


@pytest.mark.asyncio
async def test_malformed_message_moves_to_dlq_at_delivery_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = DeadLetterFakeRedis(delivery_count=3)
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    monkeypatch.setattr("src.app.event.mq_client.settings.EVENT_CONSUMER_MAX_DELIVERIES", 3)
    message = client._decode_message(("9-0", {"json": "{broken"}), redis)  # type: ignore[arg-type]

    with pytest.raises(ValueError) as caught:
        await client._handle_message(message)
    await client._handle_failure(message, caught.value)

    assert redis.dead_letters[0]["original_message_id"] == "9-0"
    assert redis.acked == [(client._queue, "test-group", "9-0")]


@pytest.mark.asyncio
async def test_two_worker_tasks_handle_messages_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.app.event.mq_client.settings.EVENT_CONSUMER_BUFFER_SIZE", 2)
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    redis = FakeRedis()
    both_started = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def handler(_payload: dict[str, object]) -> None:
        nonlocal active
        active += 1
        if active == 2:
            both_started.set()
        await release.wait()

    client._handler = handler
    await client._local_queue.put(Message(id="1-0", data={"type": "example"}, redis_client=redis))  # type: ignore[arg-type]
    await client._local_queue.put(Message(id="2-0", data={"type": "example"}, redis_client=redis))  # type: ignore[arg-type]
    workers = [asyncio.create_task(client._worker(index)) for index in range(2)]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert active == 2
    finally:
        release.set()
        await client._local_queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_timeout_does_not_ack_unfinished_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.app.event.mq_client.settings.EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS",
        0.01,
    )
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    redis = FakeRedis()
    started = asyncio.Event()
    never_finishes = asyncio.Event()

    async def handler(_payload: dict[str, object]) -> None:
        started.set()
        await never_finishes.wait()

    client._handler = handler
    await client._local_queue.put(
        Message(id="1-0", data={"type": "example"}, redis_client=redis)  # type: ignore[arg-type]
    )
    client._worker_tasks = [asyncio.create_task(client._worker(0))]

    await asyncio.wait_for(started.wait(), timeout=1)
    await client._cleanup()

    assert redis.acked == []


@pytest.mark.asyncio
async def test_event_manager_propagates_handler_failure() -> None:
    manager = AsyncEventManager()

    async def fail(payload: dict[str, object]) -> None:
        raise RuntimeError("handler failed")

    manager.register_handler("example", fail)

    with pytest.raises(RuntimeError, match="handler failed"):
        await manager.receive({"type": "example"})


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [{}, {"type": "unknown"}])
async def test_event_manager_rejects_unhandled_messages(message: dict[str, object]) -> None:
    manager = AsyncEventManager()

    with pytest.raises(UnhandledEventError):
        await manager.receive(message)
