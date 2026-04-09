from datetime import UTC, datetime
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from ...assets.service import ensure_assets_belong_to_owner, ensure_assets_exist, serialize_asset
from ..admin_audit_log.const import AdminAuditLogActionType, AdminAuditLogTargetType
from ..admin_audit_log.service import create_admin_audit_log
from .model import MailSignature
from .schema import MailSignatureCreate, MailSignatureRead, MailSignatureUpdate


def render_mail_signature_html(signature: MailSignature, *, avatar_url: str | None, banner_url: str | None) -> str:
    name = escape(signature.full_name or "")
    job_title = escape(signature.job_title or "")
    company_name = escape(signature.company_name or "")
    primary_email = escape(signature.primary_email or "")
    secondary_email = escape(signature.secondary_email or "")
    website = escape(signature.website or "")
    linkedin_label = escape(signature.linkedin_label or "")
    linkedin_url = escape(signature.linkedin_url or "")
    address = escape(signature.address or "")

    avatar_html = (
        f'<img src="{escape(avatar_url)}" alt="{name}" width="82" height="82" '
        'style="display:block;width:82px;height:82px;border-radius:50%;object-fit:cover;" />'
        if avatar_url
        else ""
    )
    banner_html = (
        f'<tr><td colspan="2" style="padding-top:18px;">'
        f'<img src="{escape(banner_url)}" alt="signature banner" width="640" '
        'style="display:block;width:100%;max-width:640px;height:auto;border:0;" />'
        "</td></tr>"
        if banner_url
        else ""
    )

    link_lines = []
    if primary_email:
        link_lines.append(f'<div><a href="mailto:{primary_email}" style="color:#165dff;text-decoration:none;">{primary_email}</a></div>')
    if secondary_email:
        link_lines.append(f'<div><a href="mailto:{secondary_email}" style="color:#165dff;text-decoration:none;">{secondary_email}</a></div>')
    if website:
        link_lines.append(f'<div><a href="{website}" style="color:#165dff;text-decoration:none;">{website}</a></div>')
    if linkedin_label:
        href = linkedin_url or "#"
        link_lines.append(f'<div><a href="{href}" style="color:#165dff;text-decoration:none;">{linkedin_label}</a></div>')

    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;font-family:Arial,sans-serif;color:#1f2937;">'
        "<tr>"
        f'<td valign="top" style="width:100px;padding:0 18px 0 0;">{avatar_html}</td>'
        '<td valign="top" style="padding:0;">'
        f'<div style="font-size:30px;line-height:1.1;font-weight:700;color:#111827;">{name}</div>'
        f'<div style="margin-top:8px;font-size:15px;line-height:1.6;color:#374151;">{job_title}</div>'
        f'<div style="font-size:15px;line-height:1.6;color:#374151;">{company_name}</div>'
        '<div style="margin-top:16px;height:1px;background:#cad4e3;"></div>'
        f'<div style="margin-top:14px;font-size:14px;line-height:1.8;">{"".join(link_lines)}</div>'
        f'<div style="margin-top:10px;font-size:15px;line-height:1.6;color:#374151;">{address}</div>'
        "</td>"
        "</tr>"
        f"{banner_html}"
        "</table>"
    )


async def _load_signature_assets(signature: MailSignature, db: AsyncSession) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    asset_ids = [asset_id for asset_id in [signature.avatar_asset_id, signature.banner_asset_id] if asset_id is not None]
    if not asset_ids:
        return None, None
    assets = await ensure_assets_exist(db, asset_ids=asset_ids)
    asset_map = {asset.id: serialize_asset(asset) for asset in assets}
    avatar_asset = asset_map.get(signature.avatar_asset_id) if signature.avatar_asset_id is not None else None
    banner_asset = asset_map.get(signature.banner_asset_id) if signature.banner_asset_id is not None else None
    return avatar_asset, banner_asset


async def serialize_mail_signature(signature: MailSignature, db: AsyncSession) -> dict[str, Any]:
    avatar_asset, banner_asset = await _load_signature_assets(signature, db)
    return MailSignatureRead(
        id=signature.id,
        name=signature.name,
        owner=signature.owner,
        enabled=signature.enabled,
        full_name=signature.full_name,
        job_title=signature.job_title,
        company_name=signature.company_name,
        primary_email=signature.primary_email,
        secondary_email=signature.secondary_email,
        website=signature.website,
        linkedin_label=signature.linkedin_label,
        linkedin_url=signature.linkedin_url,
        address=signature.address,
        avatar_asset_id=signature.avatar_asset_id,
        banner_asset_id=signature.banner_asset_id,
        avatar_asset=avatar_asset,
        banner_asset=banner_asset,
        html=render_mail_signature_html(
            signature,
            avatar_url=avatar_asset["preview_url"] if avatar_asset else None,
            banner_url=banner_asset["preview_url"] if banner_asset else None,
        ),
        created_at=signature.created_at,
        updated_at=signature.updated_at,
        data=signature.data or {},
    ).model_dump()


async def list_mail_signatures(db: AsyncSession, *, admin_user_id: int) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MailSignature)
        .where(
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.is_deleted.is_(False),
        )
        .order_by(MailSignature.name.asc(), MailSignature.id.asc())
    )
    return [await serialize_mail_signature(item, db) for item in result.scalars().all()]


async def get_mail_signature_model(signature_id: int, db: AsyncSession, *, admin_user_id: int) -> MailSignature:
    result = await db.execute(
        select(MailSignature).where(
            MailSignature.id == signature_id,
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.is_deleted.is_(False),
        )
    )
    signature = result.scalar_one_or_none()
    if signature is None:
        raise NotFoundException("Mail signature not found.")
    return signature


async def _validate_signature_assets(
    avatar_asset_id: int | None,
    banner_asset_id: int | None,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> None:
    asset_ids = [asset_id for asset_id in [avatar_asset_id, banner_asset_id] if asset_id is not None]
    if not asset_ids:
        return
    await ensure_assets_belong_to_owner(db, owner_type="admin_user", owner_id=admin_user_id, asset_ids=asset_ids)


async def create_mail_signature(payload: MailSignatureCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    existing = await db.execute(
        select(MailSignature).where(
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.name == payload.name,
            MailSignature.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Mail signature name already exists.")

    await _validate_signature_assets(payload.avatar_asset_id, payload.banner_asset_id, db, admin_user_id=admin_user_id)
    signature = MailSignature(
        admin_user_id=admin_user_id,
        name=payload.name,
        owner=payload.owner,
        enabled=payload.enabled,
        full_name=payload.full_name,
        job_title=payload.job_title,
        company_name=payload.company_name,
        primary_email=payload.primary_email,
        secondary_email=payload.secondary_email,
        website=payload.website,
        linkedin_label=payload.linkedin_label,
        linkedin_url=payload.linkedin_url,
        address=payload.address,
        avatar_asset_id=payload.avatar_asset_id,
        banner_asset_id=payload.banner_asset_id,
        data={},
    )
    db.add(signature)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.MAIL_SIGNATURE_CREATED.value,
        target_type=AdminAuditLogTargetType.MAIL_SIGNATURE.value,
        target_id=signature.id,
        data={"name": signature.name, "enabled": signature.enabled},
    )
    await db.refresh(signature)
    return await serialize_mail_signature(signature, db)


async def get_mail_signature(signature_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    signature = await get_mail_signature_model(signature_id, db, admin_user_id=admin_user_id)
    return await serialize_mail_signature(signature, db)


async def update_mail_signature(signature_id: int, payload: MailSignatureUpdate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    signature = await get_mail_signature_model(signature_id, db, admin_user_id=admin_user_id)
    provided_fields = payload.model_fields_set
    if payload.name and payload.name != signature.name:
        existing = await db.execute(
            select(MailSignature).where(
                MailSignature.admin_user_id == admin_user_id,
                MailSignature.name == payload.name,
                MailSignature.is_deleted.is_(False),
                MailSignature.id != signature_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Mail signature name already exists.")
        signature.name = payload.name

    for field_name in (
        "owner",
        "enabled",
        "full_name",
        "job_title",
        "company_name",
        "primary_email",
        "secondary_email",
        "website",
        "linkedin_label",
        "linkedin_url",
        "address",
        "avatar_asset_id",
        "banner_asset_id",
    ):
        if field_name not in provided_fields:
            continue
        setattr(signature, field_name, getattr(payload, field_name))

    if "avatar_asset_id" in provided_fields or "banner_asset_id" in provided_fields:
        await _validate_signature_assets(
            signature.avatar_asset_id,
            signature.banner_asset_id,
            db,
            admin_user_id=admin_user_id,
        )

    signature.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.MAIL_SIGNATURE_UPDATED.value,
        target_type=AdminAuditLogTargetType.MAIL_SIGNATURE.value,
        target_id=signature.id,
        data={"name": signature.name, "enabled": signature.enabled},
    )
    await db.refresh(signature)
    return await serialize_mail_signature(signature, db)


async def delete_mail_signature(signature_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    signature = await get_mail_signature_model(signature_id, db, admin_user_id=admin_user_id)
    signature.is_deleted = True
    signature.deleted_at = datetime.now(UTC)
    signature.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.MAIL_SIGNATURE_DELETED.value,
        target_type=AdminAuditLogTargetType.MAIL_SIGNATURE.value,
        target_id=signature.id,
        data={"name": signature.name, "enabled": signature.enabled},
    )
    return {"message": "Mail signature deleted."}
