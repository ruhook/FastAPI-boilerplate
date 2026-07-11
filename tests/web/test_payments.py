from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.application.payouts import pay_payables
from src.app.modules.payable.commands import transition_payables, upsert_pending_payable
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.schema import PayableDraft
from src.app.modules.payment.schema import PayoutDetails
from tests.helpers.talent import create_candidate_user, login_web_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_candidate_payments_only_reads_immutable_payment_rows(
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = f"candidatepayment{uuid4().hex[:8]}"
    user, password = await create_candidate_user(db_session, suffix=suffix)
    payable = await upsert_pending_payable(
        db=db_session,
        draft=PayableDraft(
            source_key=f"manual:{uuid4()}",
            payment_type="salary",
            settlement_month="2026-07",
            user_id=user.id,
            amount=Decimal("22.00"),
            user_snapshot_name=user.name,
            user_snapshot_email=user.email,
        ),
    )
    await transition_payables(
        db=db_session,
        payable_ids=[payable.id],
        target=PayableStatus.PROCESSING,
        admin_user_id=None,
    )
    await pay_payables(
        db=db_session,
        payable_ids=[payable.id],
        details=PayoutDetails(external_platform="Wise", external_transaction_no=f"candidate-{uuid4().hex}"),
        admin_user_id=None,
    )
    await db_session.commit()
    headers = await login_web_user(web_client, username=user.username, password=password)

    response = await web_client.get("/api/v1/me/payments", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["amount"] == "22.00"
    assert payload["summary"]["total_paid"] == "22.00"
    assert (await web_client.get("/api/v1/me/earnings", headers=headers)).status_code == 404
