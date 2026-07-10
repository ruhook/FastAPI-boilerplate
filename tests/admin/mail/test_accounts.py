import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.app.core.db.database import local_session
from src.app.modules.admin.mail_account.model import MailAccount

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
    assert created_account["has_auth_secret"] is True
    assert "auth_secret" not in created_account

    async with local_session() as session:
        persisted_account = (
            await session.execute(select(MailAccount).where(MailAccount.id == account_id))
        ).scalar_one()
        assert persisted_account.auth_secret is None
        assert persisted_account.auth_secret_encrypted.startswith("v1:")
        assert "smtp-auth-code" not in persisted_account.auth_secret_encrypted

    list_response = await client.get("/api/v1/mail/accounts", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_data = list_response.json()
    assert len(list_data) == 1
    assert list_data[0]["id"] == account_id
    assert list_data[0]["has_auth_secret"] is True
    assert "auth_secret" not in list_data[0]

    detail_response = await client.get(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail_data = detail_response.json()
    assert detail_data["email"] == "mailbox@example.com"
    assert detail_data["has_auth_secret"] is True
    assert "auth_secret" not in detail_data

    update_response = await client.patch(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
        json={
            "provider": "qq",
            "auth_secret": "updated-smtp-auth-code",
            "status": "enabled",
            "note": "updated from test",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_account = update_response.json()
    assert updated_account["provider"] == "qq"
    assert updated_account["status"] == "enabled"
    assert updated_account["note"] == "updated from test"
    assert updated_account["has_auth_secret"] is True
    assert "auth_secret" not in updated_account

    async with local_session() as session:
        persisted_account = (
            await session.execute(select(MailAccount).where(MailAccount.id == account_id))
        ).scalar_one()
        assert persisted_account.auth_secret is None
        assert "updated-smtp-auth-code" not in persisted_account.auth_secret_encrypted

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
