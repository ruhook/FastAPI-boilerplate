import pytest

from src.app.event import EventType
from src.app.modules.admin.mail_task import service as mail_task_service
from src.app.modules.admin.mail_task.const import MailTaskStatus

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio
async def test_mail_task_event_is_enqueued_on_caller_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def capture_enqueue(
        db: object,
        event_type: EventType,
        payload: dict[str, object],
    ) -> object:
        captured.update(db=db, event_type=event_type, payload=payload)
        return object()

    monkeypatch.setattr(mail_task_service, "enqueue_event", capture_enqueue)
    db = object()

    await mail_task_service.enqueue_mail_task_created_event(
        7,
        db,  # type: ignore[arg-type]
        admin_user_id=2,
    )

    assert captured == {
        "db": db,
        "event_type": EventType.MAIL_TASK_CREATED,
        "payload": {"mail_task_id": 7, "admin_user_id": 2},
    }


def test_ambiguous_smtp_failure_is_not_automatically_retryable() -> None:
    assert (
        mail_task_service.resolve_mail_failure_status(
            current_status=MailTaskStatus.SENDING.value,
            delivery_mode="smtp",
        )
        == MailTaskStatus.DELIVERY_UNKNOWN.value
    )
    assert (
        mail_task_service.resolve_mail_failure_status(
            current_status=MailTaskStatus.RENDERING.value,
            delivery_mode="smtp",
        )
        == MailTaskStatus.FAILED.value
    )
    assert (
        mail_task_service.resolve_mail_failure_status(
            current_status=MailTaskStatus.SENDING.value,
            delivery_mode="preview",
        )
        == MailTaskStatus.FAILED.value
    )
