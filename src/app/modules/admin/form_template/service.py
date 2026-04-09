from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from ..admin_audit_log.const import AdminAuditLogActionType, AdminAuditLogTargetType
from ..admin_audit_log.service import create_admin_audit_log
from ..dictionary.model import AdminDictionary
from .model import AdminFormTemplate
from .schema import (
    FormTemplateCreate,
    FormTemplateField,
    FormTemplateRead,
    FormTemplateUpdate,
    parse_form_template_fields,
)


def serialize_form_template(template: AdminFormTemplate) -> dict[str, Any]:
    fields = [field.model_dump() for field in parse_form_template_fields(template.fields or [], strict=False)]
    return FormTemplateRead(
        id=template.id,
        name=template.name,
        description=template.description,
        fields=fields,
        created_at=template.created_at,
        updated_at=template.updated_at,
        data=template.data or {},
    ).model_dump()


async def list_form_templates(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AdminFormTemplate)
        .where(AdminFormTemplate.is_deleted.is_(False))
        .order_by(AdminFormTemplate.name.asc(), AdminFormTemplate.id.asc())
    )
    templates = result.scalars().all()
    return [serialize_form_template(template) for template in templates]


async def get_form_template_model(template_id: int, db: AsyncSession) -> AdminFormTemplate:
    result = await db.execute(
        select(AdminFormTemplate).where(
            AdminFormTemplate.id == template_id,
            AdminFormTemplate.is_deleted.is_(False),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise NotFoundException("Form template not found.")
    return template


async def ensure_dictionaries_exist(db: AsyncSession, fields: list[FormTemplateField]) -> None:
    dictionary_ids = sorted({field.dictionaryId for field in fields if field.dictionaryId is not None})
    if not dictionary_ids:
        return
    result = await db.execute(
        select(AdminDictionary.id).where(
            AdminDictionary.id.in_(dictionary_ids),
            AdminDictionary.is_deleted.is_(False),
        )
    )
    existing_ids = set(result.scalars().all())
    missing_ids = [dictionary_id for dictionary_id in dictionary_ids if dictionary_id not in existing_ids]
    if missing_ids:
        raise NotFoundException(f"Dictionary not found: {missing_ids[0]}")


async def create_form_template(payload: FormTemplateCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    existing = await db.execute(
        select(AdminFormTemplate).where(
            AdminFormTemplate.name == payload.name,
            AdminFormTemplate.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Form template name already exists.")

    await ensure_dictionaries_exist(db, payload.fields)
    template = AdminFormTemplate(
        name=payload.name,
        description=payload.description,
        fields=[field.model_dump() for field in payload.fields],
        data={},
    )
    db.add(template)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.FORM_TEMPLATE_CREATED.value,
        target_type=AdminAuditLogTargetType.FORM_TEMPLATE.value,
        target_id=template.id,
        data={"name": template.name},
    )
    await db.refresh(template)
    return serialize_form_template(template)


async def get_form_template(template_id: int, db: AsyncSession) -> dict[str, Any]:
    template = await get_form_template_model(template_id, db)
    return serialize_form_template(template)


async def update_form_template(
    template_id: int,
    payload: FormTemplateUpdate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    template = await get_form_template_model(template_id, db)
    if payload.name and payload.name != template.name:
        existing = await db.execute(
            select(AdminFormTemplate).where(
                AdminFormTemplate.name == payload.name,
                AdminFormTemplate.is_deleted.is_(False),
                AdminFormTemplate.id != template_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Form template name already exists.")
        template.name = payload.name

    if payload.description is not None:
        template.description = payload.description

    if payload.fields is not None:
        await ensure_dictionaries_exist(db, payload.fields)
        template.fields = [field.model_dump() for field in payload.fields]

    template.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.FORM_TEMPLATE_UPDATED.value,
        target_type=AdminAuditLogTargetType.FORM_TEMPLATE.value,
        target_id=template.id,
        data={"name": template.name},
    )
    await db.refresh(template)
    return serialize_form_template(template)


async def delete_form_template(template_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    template = await get_form_template_model(template_id, db)
    template.is_deleted = True
    template.deleted_at = datetime.now(UTC)
    template.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.FORM_TEMPLATE_DELETED.value,
        target_type=AdminAuditLogTargetType.FORM_TEMPLATE.value,
        target_id=template.id,
        data={"name": template.name},
    )
    return {"message": "Form template deleted."}
