from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import BadRequestException, DuplicateValueException, NotFoundException
from ..mail_template.model import MailTemplate
from .model import MailTemplateCategory
from .schema import MailTemplateCategoryCreate, MailTemplateCategoryRead, MailTemplateCategoryUpdate


def serialize_mail_template_category(category: MailTemplateCategory) -> dict[str, Any]:
    return MailTemplateCategoryRead(
        id=category.id,
        parent_id=category.parent_id,
        name=category.name,
        sort_order=category.sort_order,
        enabled=category.enabled,
        created_at=category.created_at,
        updated_at=category.updated_at,
        data=category.data or {},
    ).model_dump()


async def list_mail_template_categories(db: AsyncSession, *, admin_user_id: int) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MailTemplateCategory)
        .where(
            MailTemplateCategory.admin_user_id == admin_user_id,
            MailTemplateCategory.is_deleted.is_(False),
        )
        .order_by(
            MailTemplateCategory.parent_id.asc(),
            MailTemplateCategory.sort_order.asc(),
            MailTemplateCategory.id.asc(),
        )
    )
    return [serialize_mail_template_category(item) for item in result.scalars().all()]


async def get_mail_template_category_model(category_id: int, db: AsyncSession, *, admin_user_id: int) -> MailTemplateCategory:
    result = await db.execute(
        select(MailTemplateCategory).where(
            MailTemplateCategory.id == category_id,
            MailTemplateCategory.admin_user_id == admin_user_id,
            MailTemplateCategory.is_deleted.is_(False),
        )
    )
    category = result.scalar_one_or_none()
    if category is None:
        raise NotFoundException("Mail template category not found.")
    return category


async def ensure_category_exists(category_id: int, db: AsyncSession, *, admin_user_id: int) -> MailTemplateCategory:
    return await get_mail_template_category_model(category_id, db, admin_user_id=admin_user_id)


async def create_mail_template_category(
    payload: MailTemplateCategoryCreate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    if payload.parent_id is not None:
        parent = await get_mail_template_category_model(payload.parent_id, db, admin_user_id=admin_user_id)
        if parent.parent_id is not None:
            raise BadRequestException("Only two levels of template categories are supported.")

    existing = await db.execute(
        select(MailTemplateCategory).where(
            MailTemplateCategory.admin_user_id == admin_user_id,
            MailTemplateCategory.parent_id == payload.parent_id,
            MailTemplateCategory.name == payload.name,
            MailTemplateCategory.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Mail template category name already exists.")

    category = MailTemplateCategory(
        admin_user_id=admin_user_id,
        parent_id=payload.parent_id,
        name=payload.name,
        sort_order=payload.sort_order,
        enabled=payload.enabled,
        data={},
    )
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return serialize_mail_template_category(category)


async def update_mail_template_category(
    category_id: int,
    payload: MailTemplateCategoryUpdate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    category = await get_mail_template_category_model(category_id, db, admin_user_id=admin_user_id)

    if payload.name and payload.name != category.name:
        existing = await db.execute(
            select(MailTemplateCategory).where(
                MailTemplateCategory.admin_user_id == admin_user_id,
                MailTemplateCategory.parent_id == category.parent_id,
                MailTemplateCategory.name == payload.name,
                MailTemplateCategory.is_deleted.is_(False),
                MailTemplateCategory.id != category_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Mail template category name already exists.")
        category.name = payload.name

    if payload.sort_order is not None:
        category.sort_order = payload.sort_order
    if payload.enabled is not None:
        category.enabled = payload.enabled

    category.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(category)
    return serialize_mail_template_category(category)


async def delete_mail_template_category(category_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    category = await get_mail_template_category_model(category_id, db, admin_user_id=admin_user_id)
    child_result = await db.execute(
        select(MailTemplateCategory.id).where(
            MailTemplateCategory.admin_user_id == admin_user_id,
            MailTemplateCategory.parent_id == category_id,
            MailTemplateCategory.is_deleted.is_(False),
        )
    )
    if child_result.first() is not None:
        raise BadRequestException("Please delete child categories first.")

    template_result = await db.execute(
        select(MailTemplate.id).where(
            MailTemplate.admin_user_id == admin_user_id,
            MailTemplate.category_id == category_id,
            MailTemplate.is_deleted.is_(False),
        )
    )
    if template_result.first() is not None:
        raise BadRequestException("Please move or delete templates under this category first.")

    category.is_deleted = True
    category.deleted_at = datetime.now(UTC)
    category.updated_at = datetime.now(UTC)
    await db.flush()
    return {"message": "Mail template category deleted."}
