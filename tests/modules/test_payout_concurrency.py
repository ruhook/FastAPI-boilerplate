import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.app.application.payouts import pay_payables
from src.app.core.db.database import local_session
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable
from src.app.modules.payment.const import PaymentEntryType
from src.app.modules.payment.model import Payment
from src.app.modules.payment.schema import PayoutDetails
from src.app.modules.user.model import User

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.no_database_cleanup]


async def _create_processing_payable(*, amount: str = "25.00") -> int:
    suffix = uuid4().hex[:12]
    async with local_session() as db:
        user = User(
            name="Payout User",
            username=f"pay{suffix}"[:20],
            email=f"pay.{suffix}@example.com",
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
            amount=Decimal(amount),
            currency="USD",
            calculation_snapshot={},
            user_snapshot_name=user.name,
            user_snapshot_email=user.email,
        )
        db.add(payable)
        await db.commit()
        return payable.id


async def test_concurrent_payout_creates_exactly_one_payment() -> None:
    payable_id = await _create_processing_payable()
    details = PayoutDetails(
        paid_at=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
        external_platform="Wise",
        external_transaction_no=f"wise-{uuid4().hex}",
        remark="July payout",
    )

    async def confirm() -> int:
        async with local_session() as db:
            result = await pay_payables(
                db=db,
                payable_ids=[payable_id],
                details=details,
                admin_user_id=None,
            )
            await db.commit()
            assert result.failed_count == 0
            assert result.items[0].payment is not None
            return result.items[0].payment.id

    first_id, second_id = await asyncio.gather(confirm(), confirm())

    assert first_id == second_id
    async with local_session() as db:
        payment_count = await db.scalar(
            select(func.count(Payment.id)).where(
                Payment.payable_id == payable_id,
                Payment.entry_type == PaymentEntryType.PAYMENT.value,
            )
        )
        payable = await db.get(Payable, payable_id)
        assert payment_count == 1
        assert payable is not None
        assert payable.status == PayableStatus.PAID.value
