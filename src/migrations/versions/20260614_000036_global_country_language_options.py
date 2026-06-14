"""expand country and language dictionary options

Revision ID: 20260614_000036
Revises: 20260614_000035
Create Date: 2026-06-14 23:50:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.modules.candidate_field.global_dictionary_options import (
    GLOBAL_COUNTRY_OPTIONS,
    GLOBAL_LANGUAGE_OPTIONS,
)

revision: str = "20260614_000036"
down_revision: str | None = "20260614_000035"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


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


def _merge_options(
    global_options: list[dict[str, str]],
    existing_options: Any,
) -> list[dict[str, Any]]:
    existing_list = existing_options if isinstance(existing_options, list) else []
    existing_by_value: dict[str, dict[str, Any]] = {}
    for option in existing_list:
        value = _option_value(option)
        if value and value not in existing_by_value:
            existing_by_value[value] = option

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in global_options:
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


def _upsert_dictionary(key: str, label: str, global_options: list[dict[str, str]]) -> None:
    bind = op.get_bind()
    dictionary_table = _dictionary_table()
    row = (
        bind.execute(
            sa.select(dictionary_table.c.id, dictionary_table.c.options)
            .where(
                dictionary_table.c.key == key,
                dictionary_table.c.is_deleted.is_(False),
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        row = (
            bind.execute(
                sa.select(dictionary_table.c.id, dictionary_table.c.options)
                .where(
                    dictionary_table.c.label == label,
                    dictionary_table.c.is_deleted.is_(False),
                )
                .limit(1)
            )
            .mappings()
            .first()
        )

    options = _merge_options(global_options, row["options"] if row is not None else [])
    if row is not None:
        bind.execute(
            dictionary_table.update()
            .where(dictionary_table.c.id == row["id"])
            .values(
                key=key,
                label=label,
                options=options,
                updated_at=sa.func.now(),
            )
        )
        return

    bind.execute(
        dictionary_table.insert().values(
            key=key,
            label=label,
            options=options,
            data={},
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
            is_deleted=False,
        )
    )


def upgrade() -> None:
    _upsert_dictionary("country", "国家", GLOBAL_COUNTRY_OPTIONS)
    _upsert_dictionary("candidate_language", "候选人语言", GLOBAL_LANGUAGE_OPTIONS)


def downgrade() -> None:
    # Dictionary options are admin-editable seed data; avoid deleting user-added values on downgrade.
    pass
