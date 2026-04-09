import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_superadmin_can_create_update_and_delete_role(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    create_response = await client.post(
        "/api/v1/settings/roles",
        headers=admin_auth_headers,
        json={
            "name": "Recruiter Lead",
            "description": "Can manage recruiter settings",
            "enabled": True,
            "permissions": ["账户管理", "权限与角色"],
        },
    )
    assert create_response.status_code == 201, create_response.text
    created_role = create_response.json()
    role_id = created_role["id"]
    assert created_role["name"] == "Recruiter Lead"

    list_response = await client.get("/api/v1/settings/roles", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_data = list_response.json()
    assert len(list_data) == 1
    assert list_data[0]["permissions"] == ["账户管理", "权限与角色"]

    update_response = await client.patch(
        f"/api/v1/settings/roles/{role_id}",
        headers=admin_auth_headers,
        json={
            "description": "Updated by integration test",
            "permissions": ["账户管理"],
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_role = update_response.json()
    assert updated_role["description"] == "Updated by integration test"
    assert updated_role["permissions"] == ["账户管理"]

    delete_response = await client.delete(f"/api/v1/settings/roles/{role_id}", headers=admin_auth_headers)
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["message"] == "Role deleted."

    list_after_delete_response = await client.get("/api/v1/settings/roles", headers=admin_auth_headers)
    assert list_after_delete_response.status_code == 200, list_after_delete_response.text
    assert list_after_delete_response.json() == []
