from datetime import UTC, datetime

import pytest

from src.app.admin.api.dependencies import require_admin_permission, require_any_admin_permission
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.admin_user.service import query_admin_accounts
from src.app.modules.admin.role.const import (
    ALL_ADMIN_PERMISSIONS,
    BUSINESS_ADMIN_PERMISSIONS,
    PERMISSION_CATALOG,
    SETTINGS_ADMIN_PERMISSIONS,
    SPECIAL_ADMIN_PERMISSIONS,
    resolve_effective_admin_permissions,
)
from src.app.modules.admin.role.model import Role
from src.app.modules.admin.role.service import sanitize_role_permissions

pytestmark = pytest.mark.no_database_cleanup


def build_admin(*, permissions: list[str], is_superuser: bool = False) -> dict:
    return {
        "id": 7,
        "is_superuser": is_superuser,
        "permissions": permissions,
    }


def test_ordinary_admin_receives_only_explicit_role_permissions() -> None:
    assert resolve_effective_admin_permissions([]) == []
    assert resolve_effective_admin_permissions(["岗位管理"]) == ["岗位管理"]


def test_superuser_still_receives_every_permission() -> None:
    assert resolve_effective_admin_permissions([], is_superuser=True) == ALL_ADMIN_PERMISSIONS


def test_permission_catalog_exposes_every_configurable_permission() -> None:
    catalog_items = [item for group in PERMISSION_CATALOG for item in group["items"]]

    assert catalog_items == [
        *BUSINESS_ADMIN_PERMISSIONS,
        *SETTINGS_ADMIN_PERMISSIONS,
        *SPECIAL_ADMIN_PERMISSIONS,
    ]


def test_role_serialization_preserves_business_and_settings_grants() -> None:
    assert sanitize_role_permissions(["岗位管理", "账户管理", "测试题判题"]) == [
        "岗位管理",
        "账户管理",
        "测试题判题",
    ]


@pytest.mark.asyncio
async def test_permission_dependency_denies_missing_grant_for_every_ordinary_admin() -> None:
    dependency = require_admin_permission("岗位管理")

    with pytest.raises(ForbiddenException, match="岗位管理"):
        await dependency(current_admin=build_admin(permissions=[]))


@pytest.mark.asyncio
async def test_permission_dependency_accepts_explicit_grant() -> None:
    dependency = require_admin_permission("岗位管理")

    current_admin = build_admin(permissions=["岗位管理"])
    assert await dependency(current_admin=current_admin) == current_admin


@pytest.mark.asyncio
async def test_any_permission_dependency_denies_when_no_grant_matches() -> None:
    dependency = require_any_admin_permission("岗位管理", "测试题判题")

    with pytest.raises(ForbiddenException, match="岗位管理 / 测试题判题"):
        await dependency(current_admin=build_admin(permissions=["合同管理"]))


@pytest.mark.asyncio
async def test_admin_account_query_loads_roles_without_n_plus_one() -> None:
    now = datetime.now(UTC)
    account = AdminUser(
        id=7,
        name="Jobs Admin",
        username="jobsadmin",
        email="jobs@example.com",
        hashed_password="unused",
        status="enabled",
        profile_image_url="https://example.com/avatar.png",
        is_superuser=False,
        role_id=3,
        data={},
    )
    account.created_at = now
    role = Role(
        id=3,
        name="Jobs",
        enabled=True,
        permissions=["岗位管理"],
        data={},
    )
    role.created_at = now

    class JoinedResult:
        def all(self):
            return [(account, role)]

    class CountingDatabase:
        execute_count = 0

        async def execute(self, _statement):
            self.execute_count += 1
            if self.execute_count > 1:
                raise AssertionError("administrator list performed an N+1 role query")
            return JoinedResult()

    database = CountingDatabase()
    result = await query_admin_accounts(
        database,  # type: ignore[arg-type]
        required_permission="岗位管理",
    )

    assert database.execute_count == 1
    assert result[0]["role_id"] == 3
    assert result[0]["role_name"] == "Jobs"
