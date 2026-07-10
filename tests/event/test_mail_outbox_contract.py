import pytest

from src.app.event import EventType
from src.app.modules.admin.mail_task import service as mail_task_service
from src.app.modules.admin.mail_task.const import MailTaskStatus
from src.app.modules.admin.mail_task.model import MailTask

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


@pytest.mark.parametrize(
    ("status", "delivery_mode", "expected"),
    [
        (MailTaskStatus.RENDERING.value, "smtp", (MailTaskStatus.RETRYING.value, True)),
        (MailTaskStatus.SENDING.value, "smtp", (MailTaskStatus.DELIVERY_UNKNOWN.value, False)),
        (MailTaskStatus.SENDING.value, "preview", (MailTaskStatus.RETRYING.value, True)),
    ],
)
def test_stale_mail_task_recovery_decision(
    status: str,
    delivery_mode: str,
    expected: tuple[str, bool],
) -> None:
    resolver = getattr(mail_task_service, "resolve_stale_mail_task_recovery", None)
    assert resolver is not None
    assert resolver(current_status=status, delivery_mode=delivery_mode) == expected


def test_mail_task_model_has_processing_lease_columns() -> None:
    assert "processing_started_at" in MailTask.__table__.columns
    assert "processing_lease_expires_at" in MailTask.__table__.columns
    assert (
        mail_task_service.resolve_mail_failure_status(
            current_status=MailTaskStatus.SENDING.value,
            delivery_mode="preview",
        )
        == MailTaskStatus.FAILED.value
    )
