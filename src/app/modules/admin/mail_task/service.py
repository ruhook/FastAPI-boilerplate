import asyncio
import logging
import mimetypes
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.db.database import local_session
from ....core.exceptions.http_exceptions import NotFoundException
from ....event import EventType, send_event
from ...assets.service import ensure_assets_exist, get_asset_file_path
from ..mail_account.model import MailAccount
from ..mail_account.service import get_mail_account_model
from ..mail_signature.model import MailSignature
from ..mail_signature.service import get_mail_signature_model
from ..mail_task.const import (
    MAIL_TASK_DATA_RESEND_FROM_TASK_ID_KEY,
    MAIL_TASK_DATA_RENDER_CONTEXT_KEY,
    MAIL_TASK_DATA_RENDERED_CONTEXT_KEY,
    MAIL_TASK_STATUS_CN_NAME_MAP,
    MailTaskStatus,
)
from ..mail_template.model import MailTemplate
from ..mail_template.schema import TOKEN_PATTERN
from ..mail_template.service import get_mail_template_model
from .model import MailTask
from .schema import MailTaskCreate, MailTaskRead

logger = logging.getLogger(__name__)


def serialize_mail_task(
    task: MailTask,
    *,
    account: MailAccount | None = None,
    template: MailTemplate | None = None,
    signature: MailSignature | None = None,
) -> dict[str, Any]:
    return MailTaskRead(
        id=task.id,
        account_id=task.account_id,
        account_email=account.email if account else None,
        template_id=task.template_id,
        template_name=template.name if template else None,
        signature_id=task.signature_id,
        signature_name=signature.name if signature else None,
        subject=task.subject,
        body_html=task.body_html,
        final_subject=task.final_subject,
        final_body_html=task.final_body_html,
        to_recipients=task.to_recipients or [],
        cc_recipients=task.cc_recipients or [],
        bcc_recipients=task.bcc_recipients or [],
        attachment_asset_ids=task.attachment_asset_ids or [],
        status=task.status,
        status_cn_name=MAIL_TASK_STATUS_CN_NAME_MAP.get(task.status, task.status),
        error_message=task.error_message,
        provider_message_id=task.provider_message_id,
        sent_at=task.sent_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        data=task.data or {},
    ).model_dump()


async def get_mail_task_model(task_id: int, db: AsyncSession) -> MailTask:
    result = await db.execute(select(MailTask).where(MailTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundException("Mail task not found.")
    return task


async def ensure_mail_task_attachment_assets(
    db: AsyncSession,
    *,
    admin_user_id: int,
    asset_ids: list[int],
) -> list[Any]:
    assets = await ensure_assets_exist(db, asset_ids=asset_ids)
    unauthorized_asset = next(
        (
            asset
            for asset in assets
            if not (
                (asset.module == "mail" and asset.owner_type == "admin_user" and asset.owner_id == admin_user_id)
                or asset.module == "job_progress"
            )
        ),
        None,
    )
    if unauthorized_asset is not None:
        raise NotFoundException(f"Asset not found: {unauthorized_asset.id}")
    return assets


async def get_mail_task_for_admin(
    task_id: int,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> tuple[MailTask, MailAccount, MailTemplate | None, MailSignature | None]:
    result = await db.execute(
        select(MailTask, MailAccount, MailTemplate, MailSignature)
        .join(
            MailAccount,
            MailAccount.id == MailTask.account_id,
        )
        .outerjoin(MailTemplate, MailTemplate.id == MailTask.template_id)
        .outerjoin(MailSignature, MailSignature.id == MailTask.signature_id)
        .where(
            MailTask.id == task_id,
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Mail task not found.")
    task, account, template, signature = row
    return task, account, template, signature


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _merge_scalar_context(target: dict[str, str], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            continue
        target[key] = _as_string(value)


def build_mail_render_context(
    task: MailTask,
    *,
    account: MailAccount,
    template: MailTemplate | None,
    signature: MailSignature | None,
) -> dict[str, str]:
    raw_context = task.data.get(MAIL_TASK_DATA_RENDER_CONTEXT_KEY, {}) if isinstance(task.data, dict) else {}
    if not isinstance(raw_context, dict):
        raw_context = {}

    first_recipient = (task.to_recipients or [{}])[0] if task.to_recipients else {}
    candidate_context = raw_context.get("candidate", {}) if isinstance(raw_context.get("candidate"), dict) else {}
    job_context = raw_context.get("job", {}) if isinstance(raw_context.get("job"), dict) else {}
    sender_context = raw_context.get("sender", {}) if isinstance(raw_context.get("sender"), dict) else {}
    company_context = raw_context.get("company", {}) if isinstance(raw_context.get("company"), dict) else {}

    context: dict[str, str] = {
        "candidate_name": _as_string(
            candidate_context.get("name")
            or first_recipient.get("name")
            or first_recipient.get("email")
        ),
        "candidate_email": _as_string(candidate_context.get("email") or first_recipient.get("email")),
        "job_title": _as_string(job_context.get("title")),
        "assessment_link": _as_string(job_context.get("assessment_link")),
        "due_date": _as_string(job_context.get("due_date")),
        "sender_name": _as_string(
            sender_context.get("name")
            or (signature.full_name if signature and signature.full_name else "")
            or account.email
        ),
        "sender_email": _as_string(sender_context.get("email") or account.email),
        "company_name": _as_string(
            company_context.get("name")
            or (signature.company_name if signature and signature.company_name else "")
        ),
        "template_name": _as_string(template.name if template else ""),
        "signature_name": _as_string(signature.name if signature else ""),
    }

    _merge_scalar_context(context, raw_context)
    _merge_scalar_context(context, candidate_context)
    _merge_scalar_context(context, job_context)
    _merge_scalar_context(context, sender_context)
    _merge_scalar_context(context, company_context)
    return context


def render_template_text(content: str, context: dict[str, str]) -> str:
    def replace_token(match: Any) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return TOKEN_PATTERN.sub(replace_token, content)


def _format_recipients(recipients: list[dict[str, str | None]]) -> list[str]:
    values: list[str] = []
    for recipient in recipients:
        email = (recipient.get("email") or "").strip()
        if not email:
            continue
        name = (recipient.get("name") or "").strip()
        values.append(formataddr((name, email)) if name else email)
    return values


def _resolve_attachment_payloads(task: MailTask, assets_by_id: dict[int, Any]) -> list[tuple[str, bytes, str]]:
    attachment_payloads: list[tuple[str, bytes, str]] = []
    for asset_id in task.attachment_asset_ids or []:
        asset = assets_by_id.get(asset_id)
        if asset is None:
            continue
        path = get_asset_file_path(asset)
        attachment_payloads.append((asset.original_name, path.read_bytes(), asset.mime_type))
    return attachment_payloads


def _send_mail_via_smtp(
    *,
    account: MailAccount,
    task: MailTask,
    final_subject: str,
    final_body_html: str,
    attachment_payloads: list[tuple[str, bytes, str]],
) -> str:
    message = EmailMessage()
    message["Subject"] = final_subject
    message["From"] = account.email

    to_headers = _format_recipients(task.to_recipients or [])
    cc_headers = _format_recipients(task.cc_recipients or [])
    bcc_headers = _format_recipients(task.bcc_recipients or [])

    if to_headers:
        message["To"] = ", ".join(to_headers)
    if cc_headers:
        message["Cc"] = ", ".join(cc_headers)

    provider_message_id = make_msgid()
    message["Message-ID"] = provider_message_id
    message.set_content("This email requires an HTML-compatible client.")
    message.add_alternative(final_body_html, subtype="html")

    for filename, content, mime_type in attachment_payloads:
        guessed_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        maintype, subtype = guessed_type.split("/", 1) if "/" in guessed_type else ("application", "octet-stream")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    smtp_context = ssl.create_default_context()
    recipients = to_headers + cc_headers + bcc_headers

    logger.info(
        "Sending mail task via SMTP",
        extra={
            "account_email": account.email,
            "smtp_host": account.smtp_host,
            "smtp_port": account.smtp_port,
            "security_mode": account.security_mode,
            "recipient_count": len(recipients),
            "recipients": recipients,
            "attachment_count": len(attachment_payloads),
        },
    )

    if account.security_mode == "ssl":
        with smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, context=smtp_context, timeout=30) as server:
            if settings.ENVIRONMENT.value == "local":
                server.set_debuglevel(1)
            server.login(account.smtp_username, account.auth_secret)
            server.send_message(message, to_addrs=recipients)
    else:
        with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30) as server:
            if settings.ENVIRONMENT.value == "local":
                server.set_debuglevel(1)
            if account.security_mode == "starttls":
                server.starttls(context=smtp_context)
            server.login(account.smtp_username, account.auth_secret)
            server.send_message(message, to_addrs=recipients)

    return provider_message_id


async def create_mail_task(payload: MailTaskCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    account = await get_mail_account_model(payload.account_id, db, admin_user_id=admin_user_id)
    template: MailTemplate | None = None
    if payload.template_id is not None:
        template = await get_mail_template_model(payload.template_id, db, admin_user_id=admin_user_id)
    signature: MailSignature | None = None
    if payload.signature_id is not None:
        signature = await get_mail_signature_model(payload.signature_id, db, admin_user_id=admin_user_id)
    await ensure_mail_task_attachment_assets(
        db,
        admin_user_id=admin_user_id,
        asset_ids=payload.attachment_asset_ids,
    )

    task = MailTask(
        account_id=payload.account_id,
        template_id=payload.template_id,
        signature_id=payload.signature_id,
        subject=payload.subject,
        body_html=payload.body_html,
        to_recipients=[item.model_dump() for item in payload.to_recipients],
        cc_recipients=[item.model_dump() for item in payload.cc_recipients],
        bcc_recipients=[item.model_dump() for item in payload.bcc_recipients],
        attachment_asset_ids=payload.attachment_asset_ids,
        status=MailTaskStatus.PENDING.value,
        data={MAIL_TASK_DATA_RENDER_CONTEXT_KEY: payload.render_context},
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    await db.commit()
    await db.refresh(task)

    try:
        await send_event(
            EventType.MAIL_TASK_CREATED,
            {
                "mail_task_id": task.id,
                "admin_user_id": admin_user_id,
            },
        )
    except Exception as exc:
        task.status = MailTaskStatus.FAILED.value
        task.error_message = f"Failed to dispatch mail task event: {exc}"
        task.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(task)

    return serialize_mail_task(task, account=account, template=template, signature=signature)


async def list_mail_tasks(db: AsyncSession, *, admin_user_id: int) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MailTask, MailAccount, MailTemplate, MailSignature)
        .join(
            MailAccount,
            MailAccount.id == MailTask.account_id,
        )
        .outerjoin(MailTemplate, MailTemplate.id == MailTask.template_id)
        .outerjoin(MailSignature, MailSignature.id == MailTask.signature_id)
        .where(
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
        .order_by(MailTask.created_at.desc(), MailTask.id.desc())
    )
    return [
        serialize_mail_task(task, account=account, template=template, signature=signature)
        for task, account, template, signature in result.all()
    ]


async def resend_mail_task(task_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    source_task, account, template, signature = await get_mail_task_for_admin(
        task_id,
        db,
        admin_user_id=admin_user_id,
    )
    await ensure_assets_belong_to_owner(
        db,
        owner_type="admin_user",
        owner_id=admin_user_id,
        asset_ids=source_task.attachment_asset_ids or [],
    )

    next_data = dict(source_task.data or {})
    next_data.pop(MAIL_TASK_DATA_RENDERED_CONTEXT_KEY, None)
    next_data[MAIL_TASK_DATA_RESEND_FROM_TASK_ID_KEY] = source_task.id

    retry_task = MailTask(
        account_id=source_task.account_id,
        template_id=source_task.template_id,
        signature_id=source_task.signature_id,
        subject=source_task.subject,
        body_html=source_task.body_html,
        to_recipients=list(source_task.to_recipients or []),
        cc_recipients=list(source_task.cc_recipients or []),
        bcc_recipients=list(source_task.bcc_recipients or []),
        attachment_asset_ids=list(source_task.attachment_asset_ids or []),
        status=MailTaskStatus.PENDING.value,
        data=next_data,
    )
    db.add(retry_task)
    await db.flush()
    await db.refresh(retry_task)
    await db.commit()
    await db.refresh(retry_task)

    try:
        await send_event(
            EventType.MAIL_TASK_CREATED,
            {
                "mail_task_id": retry_task.id,
                "admin_user_id": admin_user_id,
            },
        )
    except Exception as exc:
        retry_task.status = MailTaskStatus.FAILED.value
        retry_task.error_message = f"Failed to dispatch mail task event: {exc}"
        retry_task.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(retry_task)

    return serialize_mail_task(retry_task, account=account, template=template, signature=signature)


async def process_mail_task(task_id: int) -> None:
    async with local_session() as db:
        task = await get_mail_task_model(task_id, db)
        logger.info(
            "Begin processing mail task",
            extra={
                "mail_task_id": task.id,
                "status": task.status,
                "account_id": task.account_id,
                "template_id": task.template_id,
                "signature_id": task.signature_id,
                "to_recipients": task.to_recipients or [],
            },
        )
        if task.status not in {MailTaskStatus.PENDING.value, MailTaskStatus.RETRYING.value}:
            return

        task.status = MailTaskStatus.RENDERING.value
        task.error_message = None
        task.updated_at = datetime.now(UTC)
        await db.commit()

        account_result = await db.execute(
            select(MailAccount).where(
                MailAccount.id == task.account_id,
                MailAccount.is_deleted.is_(False),
            )
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            task.status = MailTaskStatus.FAILED.value
            task.error_message = "Mail account not found."
            task.updated_at = datetime.now(UTC)
            await db.commit()
            return

        template: MailTemplate | None = None
        if task.template_id is not None:
            template_result = await db.execute(
                select(MailTemplate).where(
                    MailTemplate.id == task.template_id,
                    MailTemplate.is_deleted.is_(False),
                )
            )
            template = template_result.scalar_one_or_none()

        signature: MailSignature | None = None
        if task.signature_id is not None:
            signature_result = await db.execute(
                select(MailSignature).where(
                    MailSignature.id == task.signature_id,
                    MailSignature.is_deleted.is_(False),
                )
            )
            signature = signature_result.scalar_one_or_none()

        if account.status != "enabled":
            task.status = MailTaskStatus.FAILED.value
            task.error_message = "Mail account is not enabled."
            task.updated_at = datetime.now(UTC)
            await db.commit()
            return

        try:
            render_context = build_mail_render_context(task, account=account, template=template, signature=signature)
            final_subject = render_template_text(task.subject, render_context)
            final_body_html = render_template_text(task.body_html, render_context)

            task.final_subject = final_subject
            task.final_body_html = final_body_html
            next_data = dict(task.data or {})
            next_data[MAIL_TASK_DATA_RENDERED_CONTEXT_KEY] = render_context
            task.data = next_data
            task.status = MailTaskStatus.SENDING.value
            task.updated_at = datetime.now(UTC)
            await db.commit()

            assets = await ensure_assets_exist(db, asset_ids=task.attachment_asset_ids or [])
            assets_by_id = {asset.id: asset for asset in assets}
            attachment_payloads = _resolve_attachment_payloads(task, assets_by_id)

            provider_message_id = await asyncio.to_thread(
                _send_mail_via_smtp,
                account=account,
                task=task,
                final_subject=final_subject,
                final_body_html=final_body_html,
                attachment_payloads=attachment_payloads,
            )

            task.status = MailTaskStatus.SENT.value
            task.provider_message_id = provider_message_id
            task.sent_at = datetime.now(UTC)
            task.updated_at = datetime.now(UTC)
            await db.commit()
        except Exception as exc:
            logger.exception(
                "Mail task sending failed",
                extra={
                    "mail_task_id": task.id,
                    "account_id": task.account_id,
                    "to_recipients": task.to_recipients or [],
                },
            )
            task.status = MailTaskStatus.FAILED.value
            task.error_message = f"{type(exc).__name__}: {exc}"
            task.updated_at = datetime.now(UTC)
            await db.commit()
