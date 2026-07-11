from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.payable.commands import create_manual_payable, transition_payables, upsert_pending_payable
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.queries import list_payables
from src.app.modules.payable.schema import ManualPayableCreateRequest, PayableDraft, PayableListQuery
from tests.helpers.talent import create_candidate_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _draft(*, user_id: int, source_key: str, amount: str, month: str = "2026-07") -> PayableDraft:
    return PayableDraft(
        source_key=source_key,
        payment_type="salary",
        settlement_month=month,
        user_id=user_id,
        amount=Decimal(amount),
        currency="USD",
        calculation_snapshot={"work_hours": "3.00"},
        user_snapshot_name="Settlement Candidate",
        user_snapshot_email="settlement@example.com",
    )


async def test_pending_payable_upsert_updates_the_existing_source_key(db_session: AsyncSession) -> None:
    user, _password = await create_candidate_user(db_session, suffix="payable-upsert")
    draft = _draft(user_id=user.id, source_key=f"salary:2026-07:{user.id}:0", amount="10.00")

    first = await upsert_pending_payable(db=db_session, draft=draft)
    second = await upsert_pending_payable(db=db_session, draft=replace(draft, amount=Decimal("12.00")))

    assert first.id == second.id
    assert second.amount == Decimal("12.00")


async def test_non_pending_payable_cannot_be_recalculated(db_session: AsyncSession) -> None:
    user, _password = await create_candidate_user(db_session, suffix="payable-locked")
    draft = _draft(user_id=user.id, source_key=f"salary:2026-07:{user.id}:0", amount="10.00")
    payable = await upsert_pending_payable(db=db_session, draft=draft)
    payable.status = PayableStatus.PROCESSING.value
    await db_session.flush()

    with pytest.raises(ConflictException, match="can no longer be recalculated"):
        await upsert_pending_payable(db=db_session, draft=replace(draft, amount=Decimal("15.00")))


async def test_payable_list_filters_and_summarizes_in_database(db_session: AsyncSession) -> None:
    user, _password = await create_candidate_user(db_session, suffix="payable-list")
    first = await upsert_pending_payable(
        db=db_session,
        draft=_draft(user_id=user.id, source_key=f"salary:2026-07:{user.id}:0", amount="10.00"),
    )
    second = await upsert_pending_payable(
        db=db_session,
        draft=_draft(user_id=user.id, source_key=f"salary:2026-07:{user.id}:1", amount="20.00"),
    )
    second.status = PayableStatus.PAID.value
    await db_session.flush()

    result = await list_payables(
        db=db_session,
        query=PayableListQuery(page=1, page_size=1, settlement_month="2026-07"),
    )

    assert result.total == 2
    assert len(result.items) == 1
    assert result.items[0].id == second.id
    assert result.summary.pending_count == 1
    assert result.summary.pending_amount == Decimal("10.00")
    assert result.summary.paid_count == 1
    assert result.summary.paid_amount == Decimal("20.00")
    assert first.id != second.id


async def test_manual_payable_starts_pending_without_a_payment(db_session: AsyncSession) -> None:
    user, _password = await create_candidate_user(db_session, suffix="payable-manual")

    payable = await create_manual_payable(
        db=db_session,
        payload=ManualPayableCreateRequest(
            payment_type="salary",
            settlement_month="2026-07",
            user_id=user.id,
            amount=Decimal("18.50"),
            currency="USD",
            remark="One-off correction",
        ),
        admin_user_id=None,
    )

    assert payable.source_key.startswith("manual:")
    assert payable.status == PayableStatus.PENDING.value
    assert payable.calculation_snapshot == {"remark": "One-off correction"}


async def test_transition_payables_updates_processing_timestamps(db_session: AsyncSession) -> None:
    user, _password = await create_candidate_user(db_session, suffix="payable-transition")
    payable = await upsert_pending_payable(
        db=db_session,
        draft=_draft(user_id=user.id, source_key=f"salary:2026-07:{user.id}:0", amount="9.00"),
    )

    [processing] = await transition_payables(
        db=db_session,
        payable_ids=[payable.id],
        target=PayableStatus.PROCESSING,
        admin_user_id=None,
    )
    assert processing.status == PayableStatus.PROCESSING.value
    assert processing.processing_started_at is not None

    [reopened] = await transition_payables(
        db=db_session,
        payable_ids=[payable.id],
        target=PayableStatus.PENDING,
        admin_user_id=None,
    )
    assert reopened.status == PayableStatus.PENDING.value
    assert reopened.processing_started_at is None
