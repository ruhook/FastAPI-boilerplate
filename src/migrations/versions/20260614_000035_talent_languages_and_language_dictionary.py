"""add talent languages and default language dictionary

Revision ID: 20260614_000035
Revises: 20260614_000034
Create Date: 2026-06-14 23:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_000035"
down_revision: str | None = "20260614_000034"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


LANGUAGE_OPTIONS: list[dict[str, str]] = [
    {"label": "Arabic", "admin_label": "阿拉伯语", "value": "Arabic"},
    {"label": "Bengali", "admin_label": "孟加拉语", "value": "Bengali"},
    {"label": "Bulgarian", "admin_label": "保加利亚语", "value": "Bulgarian"},
    {"label": "Burmese", "admin_label": "缅甸语", "value": "Burmese"},
    {"label": "Cantonese", "admin_label": "粤语", "value": "Cantonese"},
    {"label": "Chinese", "admin_label": "中文", "value": "Chinese"},
    {"label": "Czech", "admin_label": "捷克语", "value": "Czech"},
    {"label": "Danish", "admin_label": "丹麦语", "value": "Danish"},
    {"label": "Dutch", "admin_label": "荷兰语", "value": "Dutch"},
    {"label": "English", "admin_label": "英语", "value": "English"},
    {"label": "Filipino", "admin_label": "菲律宾语", "value": "Filipino"},
    {"label": "Finnish", "admin_label": "芬兰语", "value": "Finnish"},
    {"label": "French", "admin_label": "法语", "value": "French"},
    {"label": "German", "admin_label": "德语", "value": "German"},
    {"label": "Greek", "admin_label": "希腊语", "value": "Greek"},
    {"label": "Hindi", "admin_label": "印地语", "value": "Hindi"},
    {"label": "Hungarian", "admin_label": "匈牙利语", "value": "Hungarian"},
    {"label": "Indonesian", "admin_label": "印度尼西亚语", "value": "Indonesian"},
    {"label": "Italian", "admin_label": "意大利语", "value": "Italian"},
    {"label": "Japanese", "admin_label": "日语", "value": "Japanese"},
    {"label": "Korean", "admin_label": "韩语", "value": "Korean"},
    {"label": "Malay", "admin_label": "马来语", "value": "Malay"},
    {"label": "Portuguese", "admin_label": "葡萄牙语", "value": "Portuguese"},
    {"label": "Romanian", "admin_label": "罗马尼亚语", "value": "Romanian"},
    {"label": "Russian", "admin_label": "俄语", "value": "Russian"},
    {"label": "Spanish", "admin_label": "西班牙语", "value": "Spanish"},
    {"label": "Swedish", "admin_label": "瑞典语", "value": "Swedish"},
    {"label": "Tamil", "admin_label": "泰米尔语", "value": "Tamil"},
    {"label": "Thai", "admin_label": "泰语", "value": "Thai"},
    {"label": "Turkish", "admin_label": "土耳其语", "value": "Turkish"},
    {"label": "Urdu", "admin_label": "乌尔都语", "value": "Urdu"},
    {"label": "Vietnamese", "admin_label": "越南语", "value": "Vietnamese"},
]


def _backfill_talent_languages() -> None:
    bind = op.get_bind()
    talent_table = sa.table(
        "talent_profile",
        sa.column("id", sa.Integer()),
        sa.column("source_application_id", sa.Integer()),
        sa.column("native_languages", sa.Text()),
        sa.column("additional_languages", sa.Text()),
        sa.column("is_deleted", sa.Boolean()),
    )
    field_value_table = sa.table(
        "candidate_application_field_value",
        sa.column("application_id", sa.Integer()),
        sa.column("catalog_key", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_value", sa.Text()),
    )
    language_keys = ("native_languages", "additional_languages")
    rows = bind.execute(
        sa.select(
            talent_table.c.id,
            field_value_table.c.catalog_key,
            field_value_table.c.field_key,
            field_value_table.c.display_value,
        )
        .select_from(
            talent_table.join(
                field_value_table,
                field_value_table.c.application_id == talent_table.c.source_application_id,
            )
        )
        .where(
            talent_table.c.source_application_id.is_not(None),
            talent_table.c.is_deleted.is_(False),
            sa.or_(
                field_value_table.c.catalog_key.in_(language_keys),
                sa.and_(
                    field_value_table.c.catalog_key.is_(None),
                    field_value_table.c.field_key.in_(language_keys),
                ),
            ),
        )
    ).all()
    updates: dict[int, dict[str, str]] = {}
    for talent_id, catalog_key, field_key, display_value in rows:
        key = catalog_key or field_key
        if key not in language_keys:
            continue
        value = str(display_value or "").strip()
        if not value:
            continue
        updates.setdefault(int(talent_id), {})[key] = value

    for talent_id, values in updates.items():
        bind.execute(talent_table.update().where(talent_table.c.id == talent_id).values(**values))


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("talent_profile", sa.Column("native_languages", sa.Text(), nullable=True))
    op.add_column("talent_profile", sa.Column("additional_languages", sa.Text(), nullable=True))
    _backfill_talent_languages()

    dictionary_table = sa.table(
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
    existing_id = bind.execute(
        sa.select(dictionary_table.c.id)
        .where(
            dictionary_table.c.key == "candidate_language",
            dictionary_table.c.is_deleted.is_(False),
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing_id is not None:
        return

    label_match_id = bind.execute(
        sa.select(dictionary_table.c.id)
        .where(
            dictionary_table.c.label == "候选人语言",
            dictionary_table.c.is_deleted.is_(False),
        )
        .limit(1)
    ).scalar_one_or_none()
    if label_match_id is not None:
        bind.execute(
            dictionary_table.update()
            .where(dictionary_table.c.id == label_match_id)
            .values(key="candidate_language", options=LANGUAGE_OPTIONS)
        )
        return

    bind.execute(
        dictionary_table.insert().values(
            key="candidate_language",
            label="候选人语言",
            options=LANGUAGE_OPTIONS,
            data={},
            created_at=sa.func.now(),
            updated_at=None,
            is_deleted=False,
        )
    )


def downgrade() -> None:
    op.drop_column("talent_profile", "additional_languages")
    op.drop_column("talent_profile", "native_languages")
