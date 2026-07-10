from datetime import UTC, datetime, timedelta

import pytest

from src.app.event import EventType
from src.app.event.outbox import (
    OutboxStatus,
    enqueue_event,
    mark_outbox_publish_failed,
    mark_outbox_published,
)
from src.app.event.outbox_dispatcher import build_outbox_event_message
from src.app.modules.event_outbox.model import EventOutbox

pytestmark = pytest.mark.no_database_cleanup


class FakeSession:
    def __init__(self) -> None:
        self.added: list[EventOutbox] = []

    def add(self, row: EventOutbox) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        for index, row in enumerate(self.added, start=1):
            if row.id is None:
                row.id = index


def build_outbox(*, attempts: int = 0, max_attempts: int = 3) -> EventOutbox:
    now = datetime.now(UTC)
    return EventOutbox(
        id=1,
        event_id="stable-event-id",
        event_type=EventType.MAIL_TASK_CREATED.value,
        payload={"mail_task_id": 7},
        status=OutboxStatus.PROCESSING.value,
        available_at=now,
        attempt_count=attempts,
        max_attempts=max_attempts,
        lease_owner="worker",
        lease_expires_at=now + timedelta(seconds=30),
        created_at=now,
        published_at=None,
        processed_at=None,
        failed_at=None,
        last_error=None,
    )


def test_outbox_schema_has_stable_id_retry_and_lease_fields() -> None:
    columns = EventOutbox.__table__.c

    assert columns.event_id.unique is True
    assert columns.payload.nullable is False
    assert columns.available_at.nullable is False
    assert columns.attempt_count.nullable is False
    assert columns.max_attempts.nullable is False
    assert columns.lease_owner.nullable is True
    assert columns.lease_expires_at.nullable is True


@pytest.mark.asyncio
async def test_enqueue_event_adds_pending_row_without_commit_or_publish() -> None:
    db = FakeSession()

    row = await enqueue_event(
        db,  # type: ignore[arg-type]
        EventType.MAIL_TASK_CREATED,
        {"mail_task_id": 7, "admin_user_id": 2},
    )

    assert row in db.added
    assert row.status == OutboxStatus.PENDING.value
    assert row.event_id
    assert row.payload == {"mail_task_id": 7, "admin_user_id": 2}
    assert row.attempt_count == 0


def test_publish_success_clears_lease_and_marks_timestamp() -> None:
    row = build_outbox()

    mark_outbox_published(row)

    assert row.status == OutboxStatus.PUBLISHED.value
    assert row.published_at is not None
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert row.last_error is None


def test_published_message_always_uses_stable_outbox_event_id() -> None:
    row = build_outbox()
    row.payload = {"mail_task_id": 7, "event_id": "spoofed"}

    assert build_outbox_event_message(row) == {
        "mail_task_id": 7,
        "event_id": "stable-event-id",
    }


def test_publish_failure_retries_with_backoff_then_exhausts() -> None:
    retrying = build_outbox(attempts=0, max_attempts=2)
    before = datetime.now(UTC)

    mark_outbox_publish_failed(retrying, RuntimeError("redis unavailable"))

    assert retrying.status == OutboxStatus.PENDING.value
    assert retrying.attempt_count == 1
    assert retrying.available_at > before
    assert retrying.last_error == "RuntimeError"

    mark_outbox_publish_failed(retrying, RuntimeError("still unavailable"))

    assert retrying.status == OutboxStatus.FAILED.value
    assert retrying.attempt_count == 2
    assert retrying.failed_at is not None
