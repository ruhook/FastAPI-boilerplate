from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import BadRequestException, DuplicateValueException, NotFoundException
from ...assets.model import Asset
from ..mail_account.const import MAIL_ACCOUNT_PROVIDER_PRESETS
from ..mail_task.model import MailTask
from .model import MailAccount
from .schema import MailAccountCreate, MailAccountRead, MailAccountUpdate, resolve_mail_provider_settings


def serialize_mail_account(account: MailAccount) -> dict[str, Any]:
    return MailAccountRead(
        id=account.id,
        email=account.email,
        provider=account.provider,
        provider_label=str(MAIL_ACCOUNT_PROVIDER_PRESETS[account.provider]["label"]),
        smtp_username=account.smtp_username,
        smtp_host=account.smtp_host,
        smtp_port=account.smtp_port,
        security_mode=account.security_mode,
        auth_secret=account.auth_secret,
        status=account.status,
        note=account.note,
        verified_at=account.verified_at,
        last_tested_at=account.last_tested_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
        data=account.data or {},
    ).model_dump()


async def list_mail_accounts(db: AsyncSession, *, admin_user_id: int) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MailAccount)
        .where(
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
        .order_by(MailAccount.email.asc(), MailAccount.id.asc())
    )
    return [serialize_mail_account(item) for item in result.scalars().all()]


async def get_mail_account_model(account_id: int, db: AsyncSession, *, admin_user_id: int) -> MailAccount:
    result = await db.execute(
        select(MailAccount).where(
            MailAccount.id == account_id,
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise NotFoundException("Mail account not found.")
    return account


async def create_mail_account(payload: MailAccountCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    existing = await db.execute(
        select(MailAccount).where(
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.email == payload.email,
            MailAccount.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Mail account email already exists.")

    smtp_host, smtp_port, security_mode = resolve_mail_provider_settings(payload.provider)
    account = MailAccount(
        admin_user_id=admin_user_id,
        email=payload.email,
        provider=payload.provider,
        smtp_username=payload.email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        security_mode=security_mode,
        auth_secret=payload.auth_secret,
        status=payload.status,
        note=payload.note,
        data={},
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return serialize_mail_account(account)


async def get_mail_account(account_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    account = await get_mail_account_model(account_id, db, admin_user_id=admin_user_id)
    return serialize_mail_account(account)


async def update_mail_account(account_id: int, payload: MailAccountUpdate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    account = await get_mail_account_model(account_id, db, admin_user_id=admin_user_id)
    provided_fields = payload.model_fields_set

    if "email" in provided_fields and payload.email and payload.email != account.email:
        existing = await db.execute(
            select(MailAccount).where(
                MailAccount.admin_user_id == admin_user_id,
                MailAccount.email == payload.email,
                MailAccount.is_deleted.is_(False),
                MailAccount.id != account_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Mail account email already exists.")
        account.email = payload.email
        account.smtp_username = payload.email

    if "provider" in provided_fields and payload.provider and payload.provider != account.provider:
        smtp_host, smtp_port, security_mode = resolve_mail_provider_settings(payload.provider)
        account.provider = payload.provider
        account.smtp_host = smtp_host
        account.smtp_port = smtp_port
        account.security_mode = security_mode

    if "auth_secret" in provided_fields and payload.auth_secret is not None:
        account.auth_secret = payload.auth_secret

    if "status" in provided_fields and payload.status is not None:
        account.status = payload.status

    if "note" in provided_fields:
        account.note = payload.note

    account.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(account)
    return serialize_mail_account(account)


async def delete_mail_account(account_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    account = await get_mail_account_model(account_id, db, admin_user_id=admin_user_id)

    related_checks = (
        (MailTask, "该发信账号下仍有发信任务记录。"),
    )
    for model, message in related_checks:
        result = await db.execute(
            select(model.id).where(  # type: ignore[attr-defined]
                model.account_id == account_id,  # type: ignore[attr-defined]
                model.is_deleted.is_(False) if hasattr(model, "is_deleted") else True,  # type: ignore[attr-defined]
            )
        )
        if result.first() is not None:
            raise BadRequestException(message)

    asset_result = await db.execute(
        select(Asset.id).where(
            Asset.owner_type == "mail_account",
            Asset.owner_id == account_id,
            Asset.is_deleted.is_(False),
        )
    )
    if asset_result.first() is not None:
        raise BadRequestException("该发信账号下仍有已上传的资源。")

    account.is_deleted = True
    account.deleted_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    account.email = f"deleted+mail-account-{account.id}@local.invalid"
    account.smtp_username = account.email
    await db.flush()
    return {"message": "Mail account deleted."}
