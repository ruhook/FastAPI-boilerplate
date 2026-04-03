import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_superadmin_can_create_list_update_and_delete_admin_account(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    create_response = await client.post(
        "/api/v1/accounts",
        headers=admin_auth_headers,
        json={
            "name": "Ops Manager",
            "username": "opsmanager",
            "email": "opsmanager@example.com",
            "password": "OpsManager123!",
            "status": "enabled",
        },
    )
    assert create_response.status_code == 201, create_response.text
    created_account = create_response.json()
    account_id = created_account["id"]
    assert created_account["username"] == "opsmanager"
    assert created_account["temporary_password"] is None
    assert created_account["is_superuser"] is False

    list_response = await client.get("/api/v1/accounts", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_data = list_response.json()
    assert len(list_data) == 2
    assert any(account["username"] == "opsmanager" for account in list_data)

    detail_response = await client.get(f"/api/v1/accounts/{account_id}", headers=admin_auth_headers)
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["email"] == "opsmanager@example.com"

    update_response = await client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=admin_auth_headers,
        json={
            "status": "disabled",
            "note": "handled by integration test",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_account = update_response.json()
    assert updated_account["status"] == "disabled"
    assert updated_account["note"] == "handled by integration test"

    delete_response = await client.delete(f"/api/v1/accounts/{account_id}", headers=admin_auth_headers)
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["message"] == "Admin account deleted."

    list_after_delete_response = await client.get("/api/v1/accounts", headers=admin_auth_headers)
    assert list_after_delete_response.status_code == 200, list_after_delete_response.text
    remaining_accounts = list_after_delete_response.json()
    assert len(remaining_accounts) == 1
    assert all(account["username"] != "opsmanager" for account in remaining_accounts)
