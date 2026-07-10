from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_mail_asset_payload_helpers_offload_sync_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    async def fake_to_thread(function, *args, **kwargs):
        calls.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(mail_task_service.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(mail_task_service, "read_asset_content", lambda asset: b"content")
    task = SimpleNamespace(attachment_asset_ids=[1])
    asset = SimpleNamespace(id=1, original_name="resume.pdf", mime_type="application/pdf")

    attachment_resolver = getattr(mail_task_service, "async_resolve_attachment_payloads", None)
    data_url_builder = getattr(mail_task_service, "async_build_asset_data_url", None)
    assert attachment_resolver is not None
    assert data_url_builder is not None

    payloads = await attachment_resolver(task, {1: asset})
    data_url = await data_url_builder(asset)
    assert payloads == [("resume.pdf", b"content", "application/pdf")]
    assert data_url == "data:application/pdf;base64,Y29udGVudA=="
    assert calls == [mail_task_service._resolve_attachment_payloads, mail_task_service._build_asset_data_url]
    assert (
        mail_task_service.resolve_mail_failure_status(
            current_status=MailTaskStatus.SENDING.value,
            delivery_mode="preview",
        )
        == MailTaskStatus.FAILED.value
    )
