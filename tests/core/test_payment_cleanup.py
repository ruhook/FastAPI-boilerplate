from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.app.core.db.database import local_session
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable
from src.app.modules.payment.const import PaymentEntryType
from src.app.modules.payment.model import Payment
from src.app.modules.user.model import User
from tests.conftest import _clear_tables

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.no_database_cleanup]


async def test_database_cleanup_removes_self_referencing_payment_rows() -> None:
    async with local_session() as db:
        suffix = uuid4().hex[:12]
        user = User(
            name="Cleanup User",
            username=f"clean{suffix}"[:20],
            email=f"clean.{suffix}@example.com",
            hashed_password="test-hash",
            profile_image_url="https://example.com/profile.png",
            data={},
        )
        db.add(user)
        await db.flush()
        payable = Payable(
            source_key=f"manual:{uuid4()}",
            payment_type="salary",
            status=PayableStatus.REVERSED.value,
            settlement_month="2026-07",
            user_id=user.id,
            amount=Decimal("5.00"),
            currency="USD",
            calculation_snapshot={},
        )
        db.add(payable)
        await db.flush()
        original = Payment(
            payable_id=payable.id,
            entry_type=PaymentEntryType.PAYMENT.value,
            user_id=user.id,
            payment_type="salary",
            amount=Decimal("5.00"),
            currency="USD",
            paid_at=datetime.now(UTC),
        )
        db.add(original)
        await db.flush()
        db.add(
            Payment(
                payable_id=payable.id,
                entry_type=PaymentEntryType.REVERSAL.value,
                reversal_of_payment_id=original.id,
                user_id=user.id,
                payment_type="salary",
                amount=Decimal("-5.00"),
                currency="USD",
                paid_at=datetime.now(UTC),
            )
        )
        await db.commit()

        reversal_count = await db.scalar(
            select(func.count(Payment.id)).where(Payment.reversal_of_payment_id.is_not(None))
        )
        assert reversal_count == 1

    await _clear_tables()

    async with local_session() as db:
        assert await db.scalar(select(func.count(Payment.id))) == 0
