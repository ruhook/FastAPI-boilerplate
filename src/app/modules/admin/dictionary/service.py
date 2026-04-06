from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import BadRequestException, DuplicateValueException, NotFoundException
from ..form_template.model import AdminFormTemplate
from ..form_template.schema import parse_form_template_fields
from .model import AdminDictionary
from .schema import DictionaryCreate, DictionaryOption, DictionaryRead, DictionaryUpdate


def serialize_dictionary(dictionary: AdminDictionary) -> dict[str, Any]:
    options = [option.model_dump() for option in dictionary_options_from_model(dictionary)]
    return DictionaryRead(
        id=dictionary.id,
        key=dictionary.key,
        label=dictionary.label,
        options=options,
        created_at=dictionary.created_at,
        updated_at=dictionary.updated_at,
        data=dictionary.data or {},
    ).model_dump()


def dictionary_options_from_model(dictionary: AdminDictionary) -> list[DictionaryOption]:
    raw_options = dictionary.options or []
    normalized: list[DictionaryOption] = []
    for raw_option in raw_options:
        try:
            normalized.append(DictionaryOption.model_validate(raw_option))
        except Exception:
            continue
    return normalized


async def list_dictionaries(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AdminDictionary)
        .where(AdminDictionary.is_deleted.is_(False))
        .order_by(AdminDictionary.label.asc(), AdminDictionary.id.asc())
    )
    dictionaries = result.scalars().all()
    return [serialize_dictionary(dictionary) for dictionary in dictionaries]


async def get_dictionary_model(dictionary_id: int, db: AsyncSession) -> AdminDictionary:
    result = await db.execute(
        select(AdminDictionary).where(
            AdminDictionary.id == dictionary_id,
            AdminDictionary.is_deleted.is_(False),
        )
    )
    dictionary = result.scalar_one_or_none()
    if dictionary is None:
        raise NotFoundException("Dictionary not found.")
    return dictionary


async def create_dictionary(payload: DictionaryCreate, db: AsyncSession) -> dict[str, Any]:
    if payload.key:
        existing_by_key = await db.execute(
            select(AdminDictionary).where(
                AdminDictionary.key == payload.key,
                AdminDictionary.is_deleted.is_(False),
            )
        )
        if existing_by_key.scalar_one_or_none() is not None:
            raise DuplicateValueException("Dictionary key already exists.")

    existing = await db.execute(
        select(AdminDictionary).where(
            AdminDictionary.label == payload.label,
            AdminDictionary.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Dictionary label already exists.")

    dictionary = AdminDictionary(
        key=payload.key,
        label=payload.label,
        options=[option.model_dump() for option in payload.options],
        data={},
    )
    db.add(dictionary)
    await db.flush()
    await db.refresh(dictionary)
    return serialize_dictionary(dictionary)


async def update_dictionary(dictionary_id: int, payload: DictionaryUpdate, db: AsyncSession) -> dict[str, Any]:
    dictionary = await get_dictionary_model(dictionary_id, db)
    if payload.key != dictionary.key:
        if payload.key:
            existing_by_key = await db.execute(
                select(AdminDictionary).where(
                    AdminDictionary.key == payload.key,
                    AdminDictionary.is_deleted.is_(False),
                    AdminDictionary.id != dictionary_id,
                )
            )
            if existing_by_key.scalar_one_or_none() is not None:
                raise DuplicateValueException("Dictionary key already exists.")
        dictionary.key = payload.key

    if payload.label and payload.label != dictionary.label:
        existing = await db.execute(
            select(AdminDictionary).where(
                AdminDictionary.label == payload.label,
                AdminDictionary.is_deleted.is_(False),
                AdminDictionary.id != dictionary_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Dictionary label already exists.")
        dictionary.label = payload.label

    if payload.options is not None:
        dictionary.options = [option.model_dump() for option in payload.options]

    dictionary.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(dictionary)
    return serialize_dictionary(dictionary)


async def delete_dictionary(dictionary_id: int, db: AsyncSession) -> dict[str, str]:
    dictionary = await get_dictionary_model(dictionary_id, db)
    result = await db.execute(
        select(AdminFormTemplate).where(AdminFormTemplate.is_deleted.is_(False))
    )
    templates = result.scalars().all()
    for template in templates:
        for field in parse_form_template_fields(template.fields or [], strict=False):
            if field.dictionaryId == dictionary_id:
                raise BadRequestException("Dictionary is still used by form templates.")

    dictionary.is_deleted = True
    dictionary.deleted_at = datetime.now(UTC)
    dictionary.updated_at = datetime.now(UTC)
    await db.flush()
    return {"message": "Dictionary deleted."}
