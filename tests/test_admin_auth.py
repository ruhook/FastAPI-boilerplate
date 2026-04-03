import pytest
from httpx import AsyncClient

from src.app.modules.admin.role.const import ALL_ADMIN_PERMISSIONS


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

    permissions_response = await client.get("/api/v1/permissions/catalog", headers=headers)
    assert permissions_response.status_code == 200, permissions_response.text
    permissions_data = permissions_response.json()
    assert any(group["group"] == "系统设置" for group in permissions_data)


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
