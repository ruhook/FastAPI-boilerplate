from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers.talent import create_candidate_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_payable_to_payment_and_reversal_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
) -> None:
    user, _password = await create_candidate_user(db_session, suffix=f"admin-payable-{uuid4().hex[:8]}")
    transaction_no = f"pay-{uuid4().hex}"

    create_response = await client.post(
        "/api/v1/payables/manual",
        headers=admin_auth_headers,
        json={
            "payment_type": "salary",
            "settlement_month": "2026-07",
            "user_id": user.id,
            "amount": "31.50",
            "currency": "USD",
            "remark": "Manual payable",
        },
    )
    assert create_response.status_code == 200, create_response.text
    payable = create_response.json()
    assert payable["status"] == "pending"

    list_response = await client.get(
        "/api/v1/payables",
        headers=admin_auth_headers,
        params={"settlement_month": "2026-07"},
    )
    assert list_response.status_code == 200, list_response.text
    assert any(item["id"] == payable["id"] for item in list_response.json()["items"])

    processing_response = await client.post(
        "/api/v1/payables/processing",
        headers=admin_auth_headers,
        json={"payable_ids": [payable["id"]]},
    )
    assert processing_response.status_code == 200, processing_response.text
    assert processing_response.json()["items"][0]["status"] == "processing"

    pay_response = await client.post(
        "/api/v1/payables/pay",
        headers=admin_auth_headers,
        json={
            "payable_ids": [payable["id"]],
            "external_platform": "Wise",
            "external_transaction_no": transaction_no,
            "remark": "Paid",
        },
    )
    assert pay_response.status_code == 200, pay_response.text
    payment = pay_response.json()["items"][0]["payment"]
    assert payment["amount"] == "31.50"

    payments_response = await client.get(
        "/api/v1/payments",
        headers=admin_auth_headers,
        params={"user_id": user.id},
    )
    assert payments_response.status_code == 200, payments_response.text
    assert payments_response.json()["items"][0]["id"] == payment["id"]

    reversal_response = await client.post(
        f"/api/v1/payments/{payment['id']}/reverse",
        headers=admin_auth_headers,
        json={
            "external_platform": "Wise",
            "external_transaction_no": f"reverse-{transaction_no}",
            "remark": "Returned",
        },
    )
    assert reversal_response.status_code == 200, reversal_response.text
    assert reversal_response.json()["amount"] == "-31.50"
    assert reversal_response.json()["entry_type"] == "reversal"


async def test_old_payment_record_routes_are_not_registered(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    response = await client.get("/api/v1/payment-records", headers=admin_auth_headers)

    assert response.status_code == 404
