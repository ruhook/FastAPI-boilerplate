import importlib
import importlib.util
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_task.const import MailTaskStatus
from src.app.modules.admin.mail_task.model import MailTask
from src.app.modules.event_outbox.model import EventOutbox

pytestmark = pytest.mark.asyncio(loop_scope="session")


def load_recovery_function() -> Callable[..., Awaitable[int]]:
    module_name = "src.app.modules.admin.mail_task.recovery"
    assert importlib.util.find_spec(module_name) is not None
    module = importlib.import_module(module_name)
    recovery = getattr(module, "recover_stale_mail_tasks", None)
    assert callable(recovery)
    return recovery


async def seed_task(
    db: AsyncSession,
    *,
    status: str,
    lease_expires_at: datetime | None,
) -> MailTask:
    account = MailAccount(
        admin_user_id=None,
        email="recovery@example.com",
        provider="qq",
        smtp_username="recovery@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        security_mode="ssl",
        auth_secret_encrypted="v1:not-used-by-recovery",
        status="enabled",
        note=None,
        data={},
    )
    db.add(account)
    await db.flush()
    task = MailTask(
        account_id=account.id,
        template_id=None,
        signature_id=None,
        subject="Recovery test",
        body_html="<p>Recovery test</p>",
        to_recipients=[{"email": "candidate@example.com"}],
        cc_recipients=[],
        bcc_recipients=[],
        attachment_asset_ids=[],
        status=status,
        processing_started_at=(lease_expires_at - timedelta(seconds=60)) if lease_expires_at else None,
        processing_lease_expires_at=lease_expires_at,
        data={},
    )
    db.add(task)
    await db.flush()
    return task


async def test_recovery_requeues_stale_rendering_task(db_session: AsyncSession) -> None:
    recover_stale_mail_tasks = load_recovery_function()
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.RENDERING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )

    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )

    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.RETRYING.value
    assert task.processing_lease_expires_at is None
    outbox_rows = list((await db_session.execute(select(EventOutbox))).scalars().all())
    assert [row.payload["mail_task_id"] for row in outbox_rows] == [task.id]


async def test_recovery_marks_stale_smtp_send_unknown_without_retry(db_session: AsyncSession) -> None:
    recover_stale_mail_tasks = load_recovery_function()
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.SENDING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )

    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )

    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.DELIVERY_UNKNOWN.value
    assert await db_session.scalar(select(func.count(EventOutbox.id))) == 0


async def test_recovery_requeues_stale_preview_send(db_session: AsyncSession) -> None:
    recover_stale_mail_tasks = load_recovery_function()
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.SENDING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )

    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="preview",
        limit=10,
    )

    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.RETRYING.value
    assert await db_session.scalar(select(func.count(EventOutbox.id))) == 1


@pytest.mark.parametrize(
    ("status", "lease_offset_seconds"),
    [
        (MailTaskStatus.RENDERING.value, 60),
        (MailTaskStatus.SENT.value, -1),
    ],
)
async def test_recovery_ignores_fresh_and_terminal_tasks(
    db_session: AsyncSession,
    status: str,
    lease_offset_seconds: int,
) -> None:
    recover_stale_mail_tasks = load_recovery_function()
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=status,
        lease_expires_at=now + timedelta(seconds=lease_offset_seconds),
    )

    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )

    await db_session.refresh(task)
    assert count == 0
    assert task.status == status
