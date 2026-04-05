import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_superadmin_can_create_update_list_and_delete_mail_account(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    create_response = await client.post(
        "/api/v1/mail/accounts",
        headers=admin_auth_headers,
        json={
            "email": "mailbox@example.com",
            "provider": "qq",
            "auth_secret": "smtp-auth-code",
            "status": "pending",
            "note": "mail account integration test",
        },
    )
    assert create_response.status_code == 201, create_response.text
    created_account = create_response.json()
    account_id = created_account["id"]
    assert created_account["email"] == "mailbox@example.com"
    assert created_account["provider"] == "qq"
    assert created_account["provider_label"]
    assert created_account["smtp_host"]
    assert created_account["smtp_port"] > 0
    assert created_account["data"] == {}

    list_response = await client.get("/api/v1/mail/accounts", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_data = list_response.json()
    assert len(list_data) == 1
    assert list_data[0]["id"] == account_id

    detail_response = await client.get(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail_data = detail_response.json()
    assert detail_data["email"] == "mailbox@example.com"

    update_response = await client.patch(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
        json={
            "provider": "gmail",
            "status": "enabled",
            "note": "updated from test",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_account = update_response.json()
    assert updated_account["provider"] == "gmail"
    assert updated_account["status"] == "enabled"
    assert updated_account["note"] == "updated from test"

    variables_response = await client.get("/api/v1/mail/variables", headers=admin_auth_headers)
    assert variables_response.status_code == 200, variables_response.text
    variable_items = variables_response.json()["items"]
    assert any(item["key"] == "candidate_name" for item in variable_items)

    delete_response = await client.delete(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["message"] == "Mail account deleted."

    list_after_delete_response = await client.get("/api/v1/mail/accounts", headers=admin_auth_headers)
    assert list_after_delete_response.status_code == 200, list_after_delete_response.text
    assert list_after_delete_response.json() == []
