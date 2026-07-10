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
async def test_mq_reclaims_stale_pending_message_before_reading_new_work() -> None:
    redis = FakeClaimRedis()
    client = AsyncMQClient(QueueType.MISC, group="test-group")

    message = await client._get_item(redis)  # type: ignore[arg-type]

    assert message is not None
    assert message.id == "9-0"
    assert message.data["event_id"] == "stable-id"
    assert redis.claim_calls == 1
    assert redis.read_calls == 0


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
