"""make administrator permissions explicit

Revision ID: 20260710_000044
Revises: 20260710_000043
Create Date: 2026-07-10 20:30:00.000000
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000044"
down_revision: str | None = "20260710_000043"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

FULL_ACCESS_ROLE_NAME = "Existing Full Access"
DEFAULT_PERMISSIONS = [
    "工作台",
    "岗位管理",
    "合同管理",
    "工时记录",
    "流水记录",
    "内推奖金",
    "总人才库",
    "邮件与模板",
    "账户管理",
    "权限与角色",
    "常量字典",
    "报名表单策略",
    "公司管理",
]
SPECIAL_PERMISSIONS = ["测试题判题"]
ALL_PERMISSIONS = [*DEFAULT_PERMISSIONS, *SPECIAL_PERMISSIONS]

role = sa.table(
    "role",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String()),
    sa.column("description", sa.String()),
    sa.column("enabled", sa.Boolean()),
    sa.column("permissions", sa.JSON()),
    sa.column("data", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
admin_user = sa.table(
    "admin_user",
    sa.column("role_id", sa.Integer()),
    sa.column("is_superuser", sa.Boolean()),
)


def _parse_permissions(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item in ALL_PERMISSIONS]


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def upgrade() -> None:
    connection = op.get_bind()
    role_rows = connection.execute(sa.select(role.c.id, role.c.name, role.c.permissions)).all()
    full_access_role_id: int | None = None

    for role_id, role_name, stored_permissions in role_rows:
        explicit_permissions = _parse_permissions(stored_permissions)
        if explicit_permissions == SPECIAL_PERMISSIONS:
            migrated_permissions = SPECIAL_PERMISSIONS
        else:
            migrated_permissions = _deduplicate([*DEFAULT_PERMISSIONS, *explicit_permissions])
        connection.execute(
            role.update().where(role.c.id == role_id).values(permissions=migrated_permissions)
        )
        if role_name == FULL_ACCESS_ROLE_NAME:
            full_access_role_id = int(role_id)

    if full_access_role_id is None:
        now = datetime.now(UTC)
        connection.execute(
            role.insert().values(
                name=FULL_ACCESS_ROLE_NAME,
                description="Compatibility role created when strict administrator permissions were enabled.",
                enabled=True,
                permissions=DEFAULT_PERMISSIONS,
                data={"created_by_migration": revision},
                created_at=now,
                updated_at=now,
            )
        )
        full_access_role_id = int(
            connection.execute(
                sa.select(role.c.id).where(role.c.name == FULL_ACCESS_ROLE_NAME)
            ).scalar_one()
        )

    connection.execute(
        admin_user.update()
        .where(admin_user.c.is_superuser.is_(False), admin_user.c.role_id.is_(None))
        .values(role_id=full_access_role_id)
    )


def downgrade() -> None:
    connection = op.get_bind()
    full_access_role_id = connection.execute(
        sa.select(role.c.id).where(role.c.name == FULL_ACCESS_ROLE_NAME)
    ).scalar_one_or_none()
    if full_access_role_id is None:
        return
    connection.execute(
        admin_user.update()
        .where(admin_user.c.role_id == full_access_role_id)
        .values(role_id=None)
    )
    connection.execute(role.delete().where(role.c.id == full_access_role_id))
