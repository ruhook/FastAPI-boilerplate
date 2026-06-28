"""seed progress language dictionaries

Revision ID: 20260627_000038
Revises: 20260625_000037
Create Date: 2026-06-27 18:30:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_000038"
down_revision: str | None = "20260625_000037"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

JOB_LANGUAGE_REQUIREMENT_VALUES = [
    "English",
    "Indonesian",
    "Vietnamese",
    "Thai",
    "Malay",
    "Japanese",
    "Korean",
    "Filipino",
    "Arabic",
    "Russian",
    "French",
    "German",
    "Portuguese",
    "Spanish",
    "Italian",
    "Afrikaans",
    "Azerbaijani",
    "Bulgarian",
    "Bengali",
    "Catalan",
    "Cantonese",
    "Cebuano",
    "Czech",
    "Danish",
    "Greek",
    "Estonian",
    "Persian",
    "Finnish",
    "Irish",
    "Gujarati",
    "Hebrew",
    "Hindi",
    "Croatian",
    "Hungarian",
    "Icelandic",
    "Javanese",
    "Kazakh",
    "Khmer",
    "Kannada",
    "Lithuanian",
    "Latvian",
    "Mandarin",
    "Malayalam",
    "Marathi",
    "Burmese",
    "Dutch",
    "Norwegian",
    "Punjabi",
    "Polish",
    "Romanian",
    "Slovak",
    "Slovenian",
    "Albanian",
    "Serbian",
    "Swedish",
    "Swahili",
    "Tamil",
    "Telugu",
    "Turkish",
    "Ukrainian",
    "Urdu",
    "Uzbek",
    "Traditional Chinese",
]

JOB_LANGUAGE_REQUIREMENT_OPTIONS = [
    {"label": value, "admin_label": value, "value": value}
    for value in JOB_LANGUAGE_REQUIREMENT_VALUES
]

JOB_PROGRESS_LANGUAGE_CODES = [
    "无",
    "en-UK",
    "en-EU",
    "id-ID",
    "vi-VN",
    "th-TH",
    "ms-MY",
    "ja-JP",
    "ko-KR",
    "fil-PH",
    "fil-Row",
    "ar-ME",
    "ru-RU",
    "fr-FR",
    "de-DE",
    "pt-BR",
    "es-MX",
    "it-IT",
    "af-ZA",
    "ar-MENA",
    "ar-SA",
    "ar-ZA",
    "az-AZ",
    "bg-BG",
    "bn-BD",
    "bn-IN",
    "ca-ES",
    "cantonese-HK",
    "ceb-PH",
    "cs-CZ",
    "da-DK",
    "el-GR",
    "en-AU",
    "en-CA",
    "en-HK",
    "en-SG",
    "en-US",
    "es-ES",
    "et-EE",
    "fa-IR",
    "fi-FI",
    "ga-IE",
    "gu-IN",
    "he-IL",
    "hi-IN",
    "hr-HR",
    "hu-HU",
    "is-IS",
    "jv-ID",
    "kk-KZ",
    "km-KH",
    "kn-IN",
    "lt-LT",
    "lv-LV",
    "mandarin-HK",
    "mandarin-SG",
    "ml-IN",
    "mr-IN",
    "my-MM",
    "nl-BE",
    "nl-NL",
    "no-NO",
    "pa-IN",
    "pa-PK",
    "pl-PL",
    "pt-PT",
    "ro-RO",
    "sk-SK",
    "sl-SI",
    "sq-AL",
    "sq-XK",
    "sr-RS",
    "sv-SE",
    "sw-KE",
    "sw-TZ",
    "ta-IN",
    "ta-LK",
    "te-IN",
    "tr-TR",
    "uk-UA",
    "ur-PK",
    "uz-UZ",
    "zh-HK",
    "zh-MO",
    "zh-TW",
]

JOB_PROGRESS_LANGUAGE_OPTIONS = [
    {"label": value, "admin_label": value, "value": value}
    for value in JOB_PROGRESS_LANGUAGE_CODES
]


def _dictionary_table() -> Any:
    return sa.table(
        "admin_dictionary",
        sa.column("id", sa.Integer()),
        sa.column("key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("options", sa.JSON()),
        sa.column("data", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("is_deleted", sa.Boolean()),
    )


def _option_value(option: Any) -> str:
    if not isinstance(option, dict):
        return ""
    return str(option.get("value") or option.get("label") or "").strip()


def _merge_options(seed_options: list[dict[str, str]], existing_options: Any) -> list[dict[str, Any]]:
    existing_list = existing_options if isinstance(existing_options, list) else []
    existing_by_value = {
        value: option
        for option in existing_list
        if (value := _option_value(option))
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in seed_options:
        value = _option_value(option)
        if not value or value in seen:
            continue
        merged.append(existing_by_value.get(value, option))
        seen.add(value)
    for value, option in existing_by_value.items():
        if value not in seen:
            merged.append(option)
            seen.add(value)
    return merged


def _upsert_dictionary(key: str, label: str, options: list[dict[str, str]]) -> None:
    bind = op.get_bind()
    dictionary_table = _dictionary_table()
    row = (
        bind.execute(
            sa.select(dictionary_table.c.id, dictionary_table.c.options)
            .where(dictionary_table.c.key == key, dictionary_table.c.is_deleted.is_(False))
            .limit(1)
        )
        .mappings()
        .first()
    )
    merged_options = _merge_options(options, row["options"] if row is not None else [])
    if row is not None:
        bind.execute(
            dictionary_table.update()
            .where(dictionary_table.c.id == row["id"])
            .values(label=label, options=merged_options, updated_at=sa.func.now())
        )
        return
    bind.execute(
        dictionary_table.insert().values(
            key=key,
            label=label,
            options=merged_options,
            data={},
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
            is_deleted=False,
        )
    )


def upgrade() -> None:
    _upsert_dictionary("job_language_requirement", "岗位语种要求", JOB_LANGUAGE_REQUIREMENT_OPTIONS)
    _upsert_dictionary("job_progress_language", "招聘进展语种", JOB_PROGRESS_LANGUAGE_OPTIONS)


def downgrade() -> None:
    pass
