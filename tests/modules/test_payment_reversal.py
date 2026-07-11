from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.app.application.payouts import pay_payables, reverse_payment
from src.app.core.db.database import local_session
from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable
from src.app.modules.payment.const import PaymentEntryType
from src.app.modules.payment.model import Payment
from src.app.modules.payment.schema import PayoutDetails
from src.app.modules.user.model import User

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.no_database_cleanup]


async def _create_paid_payment() -> tuple[int, int]:
    suffix = uuid4().hex[:12]
    async with local_session() as db:
        user = User(
            name="Reversal User",
            username=f"rev{suffix}"[:20],
            email=f"rev.{suffix}@example.com",
            hashed_password="test-hash",
            profile_image_url="https://example.com/profile.png",
            data={},
        )
        db.add(user)
        await db.flush()
        payable = Payable(
            source_key=f"manual:{uuid4()}",
            payment_type="salary",
            status=PayableStatus.PROCESSING.value,
            settlement_month="2026-07",
            user_id=user.id,
            amount=Decimal("40.00"),
            currency="USD",
            calculation_snapshot={},
            user_snapshot_name=user.name,
            user_snapshot_email=user.email,
        )
        db.add(payable)
        await db.flush()
        result = await pay_payables(
            db=db,
            payable_ids=[payable.id],
            details=PayoutDetails(
                paid_at=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
                external_platform="Wise",
                external_transaction_no=f"original-{suffix}",
            ),
            admin_user_id=None,
        )
        await db.commit()
        payment = result.items[0].payment
        assert payment is not None
        return payable.id, payment.id


async def test_reversal_is_negative_immutable_and_idempotent() -> None:
    payable_id, payment_id = await _create_paid_payment()
    details = PayoutDetails(
        paid_at=datetime(2026, 7, 11, 10, 0, tzinfo=UTC),
        external_platform="Wise",
        external_transaction_no=f"reversal-{uuid4().hex}",
        remark="Payment returned",
    )

    async with local_session() as db:
        first = await reverse_payment(
            db=db,
            payment_id=payment_id,
            details=details,
            admin_user_id=None,
        )
        await db.commit()

    async with local_session() as db:
        second = await reverse_payment(
            db=db,
            payment_id=payment_id,
            details=details,
            admin_user_id=None,
        )
        await db.commit()

    assert first.id == second.id
    assert first.entry_type == PaymentEntryType.REVERSAL.value
    assert first.amount == Decimal("-40.00")
    assert first.reversal_of_payment_id == payment_id

    async with local_session() as db:
        payable = await db.get(Payable, payable_id)
        payments = list(
            (await db.scalars(select(Payment).where(Payment.payable_id == payable_id))).all()
        )
        assert payable is not None
        assert payable.status == PayableStatus.REVERSED.value
        assert len(payments) == 2


async def test_repeating_reversal_with_different_transaction_conflicts() -> None:
    _payable_id, payment_id = await _create_paid_payment()
    first_details = PayoutDetails(external_platform="Wise", external_transaction_no=f"reversal-{uuid4().hex}")

    async with local_session() as db:
        await reverse_payment(db=db, payment_id=payment_id, details=first_details, admin_user_id=None)
        await db.commit()

    async with local_session() as db:
        with pytest.raises(ConflictException, match="different payout details"):
            await reverse_payment(
                db=db,
                payment_id=payment_id,
                details=PayoutDetails(external_platform="Wise", external_transaction_no=f"other-{uuid4().hex}"),
                admin_user_id=None,
            )
