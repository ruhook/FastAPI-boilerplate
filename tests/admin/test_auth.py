import pytest
from httpx import AsyncClient

from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.database import local_session
from src.app.modules.admin.admin_audit_log.const import AdminAuditLogActionType
from src.app.modules.admin.role.const import ALL_ADMIN_PERMISSIONS, DEFAULT_ADMIN_PERMISSIONS
from tests.helpers.admin import create_admin_user, fetch_admin_audit_logs

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.no_database_cleanup
async def test_local_dev_auto_login_returns_virtual_superadmin(
    client: AsyncClient,
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": "HaokangImport",
            "password": "anything-can-login-locally",
        },
    )

    assert login_response.status_code == 200, login_response.text
    login_data = login_response.json()
    assert login_data["token_type"] == "bearer"
    assert login_data["access_token"]
    assert login_data["refresh_token"]
    assert login_data["user"]["id"] == 0
    assert login_data["user"]["username"] == "HaokangImport"
    assert login_data["user"]["email"] == "haokang-import-admin@example.com"
    assert login_data["user"]["is_superuser"] is True
    assert sorted(login_data["user"]["permissions"]) == sorted(ALL_ADMIN_PERMISSIONS)

    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login_data['access_token']}"},
    )
    assert me_response.status_code == 200, me_response.text
    me_data = me_response.json()
    assert me_data["id"] == 0
    assert me_data["username"] == "HaokangImport"
    assert me_data["is_superuser"] is True
    assert sorted(me_data["permissions"]) == sorted(ALL_ADMIN_PERMISSIONS)

    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login_data["refresh_token"]},
    )
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_data = refresh_response.json()
    assert refresh_data["access_token"]
    assert refresh_data["user"]["id"] == 0
    assert refresh_data["user"]["is_superuser"] is True


@pytest.mark.no_database_cleanup
async def test_dev_auto_login_account_does_not_bypass_auth_outside_local(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentOption.STAGING)

    response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": "HaokangImport",
            "password": "anything-can-login-locally",
        },
    )

    assert response.status_code == 401, response.text


async def test_admin_login_me_and_permissions_catalog(
    client: AsyncClient,
    superadmin_credentials: dict[str, str | int],
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": superadmin_credentials["email"],
            "password": superadmin_credentials["password"],
        },
    )

    assert login_response.status_code == 200, login_response.text
    login_data = login_response.json()
    assert login_data["token_type"] == "bearer"
    assert login_data["user"]["username"] == superadmin_credentials["username"]
    assert login_data["user"]["is_superuser"] is True
    assert sorted(login_data["user"]["permissions"]) == sorted(ALL_ADMIN_PERMISSIONS)
    assert login_data["user"]["last_login_at"] is not None

    headers = {"Authorization": f"Bearer {login_data['access_token']}"}

    me_response = await client.get("/api/v1/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    me_data = me_response.json()
    assert me_data["email"] == superadmin_credentials["email"]
    assert me_data["is_superuser"] is True

    permissions_response = await client.get("/api/v1/settings/permissions/catalog", headers=headers)
    assert permissions_response.status_code == 200, permissions_response.text
    permissions_data = permissions_response.json()
    assert permissions_data == [{"group": "特殊权限", "items": ["测试题判题"]}]

    async with local_session() as session:
        audit_logs = await fetch_admin_audit_logs(session, admin_user_id=int(superadmin_credentials["id"]))
        assert [log.action_type for log in audit_logs] == [AdminAuditLogActionType.ADMIN_LOGIN.value]


async def test_non_superadmin_without_special_role_gets_default_permissions(
    client: AsyncClient,
    db_session,
) -> None:
    admin, password = await create_admin_user(
        db_session,
        role_id=None,
        username_prefix="defaultperm",
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": admin.email,
            "password": password,
        },
    )
    assert login_response.status_code == 200, login_response.text
    permissions = login_response.json()["user"]["permissions"]
    assert sorted(permissions) == sorted(DEFAULT_ADMIN_PERMISSIONS)


async def test_admin_refresh_and_logout_blacklists_refresh_token(
    client: AsyncClient,
    superadmin_credentials: dict[str, str | int],
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": superadmin_credentials["username"],
            "password": superadmin_credentials["password"],
        },
    )
    assert login_response.status_code == 200, login_response.text
    login_data = login_response.json()

    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login_data["refresh_token"]},
    )
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_data = refresh_response.json()
    assert refresh_data["access_token"]
    assert refresh_data["refresh_token"]

    logout_response = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {refresh_data['access_token']}"},
        json={"refresh_token": refresh_data["refresh_token"]},
    )
    assert logout_response.status_code == 200, logout_response.text
    assert logout_response.json()["message"] == "Logged out successfully."

    refresh_again_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_data["refresh_token"]},
    )
    assert refresh_again_response.status_code == 200, refresh_again_response.text


async def test_admin_can_change_password_and_write_audit_log(
    client: AsyncClient,
    db_session,
) -> None:
    admin, original_password = await create_admin_user(
        db_session,
        role_id=None,
        username_prefix="cpadmin",
        password="AdminPass123!",
    )
    new_password = "AdminPass456!"

    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": admin.email,
            "password": original_password,
        },
    )
    assert login_response.status_code == 200, login_response.text
    access_token = login_response.json()["access_token"]

    change_password_response = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "current_password": original_password,
            "new_password": new_password,
            "confirm_new_password": new_password,
        },
    )
    assert change_password_response.status_code == 200, change_password_response.text

    relogin_response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": admin.username,
            "password": new_password,
        },
    )
    assert relogin_response.status_code == 200, relogin_response.text

    async with local_session() as session:
        audit_logs = await fetch_admin_audit_logs(session, admin_user_id=admin.id)
        action_types = [log.action_type for log in audit_logs]
        assert AdminAuditLogActionType.ADMIN_PASSWORD_CHANGED.value in action_types
