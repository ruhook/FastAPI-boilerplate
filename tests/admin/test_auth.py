import pytest
from httpx import AsyncClient

from src.app.core.db.database import local_session
from src.app.modules.admin.admin_audit_log.const import AdminAuditLogActionType
from src.app.modules.admin.role.const import ALL_ADMIN_PERMISSIONS

from tests.helpers.admin import create_admin_user, fetch_admin_audit_logs


pytestmark = pytest.mark.asyncio(loop_scope="session")


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
    assert any(group["group"] == "设置页面" for group in permissions_data)

    async with local_session() as session:
        audit_logs = await fetch_admin_audit_logs(session, admin_user_id=int(superadmin_credentials["id"]))
        assert [log.action_type for log in audit_logs] == [AdminAuditLogActionType.ADMIN_LOGIN.value]


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
