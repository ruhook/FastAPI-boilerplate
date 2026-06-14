from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin.dictionary.model import AdminDictionary
from ..admin.dictionary.service import dictionary_options_from_model
from .const import CANDIDATE_FIELD_CN_NAME_MAP, CANDIDATE_FIELD_DICTIONARY_KEY_MAP


def list_candidate_field_catalog() -> list[dict[str, str | None]]:
    return [
        {
            "key": field_key.value,
            "label": label,
            "dictionary_key": CANDIDATE_FIELD_DICTIONARY_KEY_MAP.get(field_key),
        }
        for field_key, label in CANDIDATE_FIELD_CN_NAME_MAP.items()
    ]


def _normalize_dictionary_id(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


def _normalize_dictionary_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_existing_options(raw_options: object) -> list[dict[str, str]] | None:
    if not isinstance(raw_options, list):
        return None

    normalized: list[dict[str, str]] = []
    for item in raw_options:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if label and value:
                normalized.append({"label": label, "value": value})
            continue

        if isinstance(item, str):
            value = item.strip()
            if value:
                normalized.append({"label": value, "value": value})

    return normalized or None


async def hydrate_candidate_field_options(
    form_fields: list[dict[str, object]],
    *,
    db: AsyncSession,
) -> list[dict[str, object]]:
    hydrated_fields: list[dict[str, object]] = []
    dictionary_ids = sorted(
        {
            dictionary_id
            for dictionary_id in (
                _normalize_dictionary_id(field.get("dictionaryId")) for field in form_fields
            )
            if dictionary_id is not None
        }
    )
    dictionary_keys = sorted(
        {
            dictionary_key
            for dictionary_key in (
                _normalize_dictionary_key(field.get("dictionary_key")) for field in form_fields
            )
            if dictionary_key is not None
        }
    )

    dictionary_option_map: dict[int, list[dict[str, str]]] = {}
    dictionary_option_key_map: dict[str, list[dict[str, str]]] = {}
    if dictionary_ids:
        result = await db.execute(
            select(AdminDictionary).where(
                AdminDictionary.id.in_(dictionary_ids),
                AdminDictionary.is_deleted.is_(False),
            )
        )
        for dictionary in result.scalars().all():
            dictionary_option_map[dictionary.id] = [
                option.model_dump() for option in dictionary_options_from_model(dictionary)
            ]
    if dictionary_keys:
        result = await db.execute(
            select(AdminDictionary).where(
                AdminDictionary.key.in_(dictionary_keys),
                AdminDictionary.is_deleted.is_(False),
            )
        )
        for dictionary in result.scalars().all():
            if dictionary.key:
                dictionary_option_key_map[dictionary.key] = [
                    option.model_dump() for option in dictionary_options_from_model(dictionary)
                ]

    for raw_field in form_fields:
        field = dict(raw_field)
        field["visible"] = field.get("visible", True) is not False
        dictionary_id = _normalize_dictionary_id(field.get("dictionaryId"))
        dictionary_key = _normalize_dictionary_key(field.get("dictionary_key"))
        if dictionary_id is not None and dictionary_id in dictionary_option_map:
            field["options"] = dictionary_option_map[dictionary_id]
        elif dictionary_key is not None and dictionary_key in dictionary_option_key_map:
            field["options"] = dictionary_option_key_map[dictionary_key]
        else:
            normalized_options = _normalize_existing_options(field.get("options"))
            if normalized_options:
                field["options"] = normalized_options
        hydrated_fields.append(field)

    return hydrated_fields
