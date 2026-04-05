from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from ...assets.service import ensure_assets_belong_to_owner, ensure_assets_exist, serialize_asset
from ..mail_template_category.service import ensure_category_exists
from .model import MailTemplate
from .schema import (
    MailTemplateAttachmentRead,
    MailTemplateCreate,
    MailTemplateRead,
    MailTemplateUpdate,
    extract_template_variables,
)


def serialize_mail_template(
    template: MailTemplate,
    attachment_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    return MailTemplateRead(
        id=template.id,
        category_id=template.category_id,
        name=template.name,
        subject_template=template.subject_template,
        body_html=template.body_html,
        attachments=[MailTemplateAttachmentRead(**item) for item in attachment_payloads],
        variables=extract_template_variables(template.subject_template, template.body_html),
        created_at=template.created_at,
        updated_at=template.updated_at,
        data=template.data or {},
    ).model_dump()


async def _serialize_template_with_assets(template: MailTemplate, db: AsyncSession) -> dict[str, Any]:
    attachment_ids = [int(item["asset_id"]) for item in (template.attachments or []) if "asset_id" in item]
    assets = await ensure_assets_exist(db, asset_ids=attachment_ids)
    asset_map = {asset.id: serialize_asset(asset) for asset in assets}
    attachment_payloads = [
        {
            "asset_id": asset_id,
            "name": asset_map[asset_id]["original_name"],
            "mime_type": asset_map[asset_id]["mime_type"],
            "preview_url": asset_map[asset_id]["preview_url"],
            "download_url": asset_map[asset_id]["download_url"],
        }
        for asset_id in attachment_ids
        if asset_id in asset_map
    ]
    return serialize_mail_template(template, attachment_payloads)


async def list_mail_templates(db: AsyncSession, *, admin_user_id: int) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MailTemplate)
        .where(
            MailTemplate.admin_user_id == admin_user_id,
            MailTemplate.is_deleted.is_(False),
        )
        .order_by(MailTemplate.name.asc(), MailTemplate.id.asc())
    )
    templates = result.scalars().all()
    return [await _serialize_template_with_assets(item, db) for item in templates]


async def get_mail_template_model(template_id: int, db: AsyncSession, *, admin_user_id: int) -> MailTemplate:
    result = await db.execute(
        select(MailTemplate).where(
            MailTemplate.id == template_id,
            MailTemplate.admin_user_id == admin_user_id,
            MailTemplate.is_deleted.is_(False),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise NotFoundException("Mail template not found.")
    return template


async def create_mail_template(payload: MailTemplateCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    await ensure_category_exists(payload.category_id, db, admin_user_id=admin_user_id)
    existing = await db.execute(
        select(MailTemplate).where(
            MailTemplate.admin_user_id == admin_user_id,
            MailTemplate.name == payload.name,
            MailTemplate.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Mail template name already exists.")

    attachment_ids = [item.asset_id for item in payload.attachments]
    await ensure_assets_belong_to_owner(db, owner_type="admin_user", owner_id=admin_user_id, asset_ids=attachment_ids)
    template = MailTemplate(
        admin_user_id=admin_user_id,
        category_id=payload.category_id,
        name=payload.name,
        subject_template=payload.subject_template,
        body_html=payload.body_html,
        attachments=[{"asset_id": asset_id} for asset_id in attachment_ids],
        data={},
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return await _serialize_template_with_assets(template, db)


async def get_mail_template(template_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    template = await get_mail_template_model(template_id, db, admin_user_id=admin_user_id)
    return await _serialize_template_with_assets(template, db)


async def update_mail_template(template_id: int, payload: MailTemplateUpdate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    template = await get_mail_template_model(template_id, db, admin_user_id=admin_user_id)

    if payload.category_id is not None:
        await ensure_category_exists(payload.category_id, db, admin_user_id=admin_user_id)
        template.category_id = payload.category_id

    if payload.name and payload.name != template.name:
        existing = await db.execute(
            select(MailTemplate).where(
                MailTemplate.admin_user_id == admin_user_id,
                MailTemplate.name == payload.name,
                MailTemplate.is_deleted.is_(False),
                MailTemplate.id != template_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Mail template name already exists.")
        template.name = payload.name

    if payload.subject_template is not None:
        template.subject_template = payload.subject_template
    if payload.body_html is not None:
        template.body_html = payload.body_html
    if payload.attachments is not None:
        attachment_ids = [item.asset_id for item in payload.attachments]
        await ensure_assets_belong_to_owner(db, owner_type="admin_user", owner_id=admin_user_id, asset_ids=attachment_ids)
        template.attachments = [{"asset_id": asset_id} for asset_id in attachment_ids]

    template.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(template)
    return await _serialize_template_with_assets(template, db)


async def delete_mail_template(template_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    template = await get_mail_template_model(template_id, db, admin_user_id=admin_user_id)
    template.is_deleted = True
    template.deleted_at = datetime.now(UTC)
    template.updated_at = datetime.now(UTC)
    await db.flush()
    return {"message": "Mail template deleted."}
