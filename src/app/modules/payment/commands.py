from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ConflictException, NotFoundException
from ..payable.const import PayableStatus
from ..payable.model import Payable
from ..payable.policy import ensure_payable_transition
from .const import PaymentEntryType
from .model import Payment
from .schema import PayoutDetails


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _details_match(payment: Payment, details: PayoutDetails) -> bool:
    if payment.external_platform != details.external_platform:
        return False
    if payment.external_transaction_no != details.external_transaction_no:
        return False
    if payment.remark != details.remark:
        return False
    return details.paid_at is None or _utc_naive(payment.paid_at) == _utc_naive(details.paid_at)


def _copy_payable_to_payment(
    *,
    payable: Payable,
    details: PayoutDetails,
    admin_user_id: int | None,
    entry_type: PaymentEntryType,
    amount: Decimal,
    reversal_of_payment_id: int | None = None,
) -> Payment:
    return Payment(
        payable_id=payable.id,
        entry_type=entry_type.value,
        reversal_of_payment_id=reversal_of_payment_id,
        user_id=payable.user_id,
        talent_profile_id=payable.talent_profile_id,
        contract_record_id=payable.contract_record_id,
        referral_record_id=payable.referral_record_id,
        company_id=payable.company_id,
        project_id=payable.project_id,
        referral_referred_user_id=payable.referral_referred_user_id,
        payment_type=payable.payment_type,
        amount=amount,
        currency=payable.currency,
        paid_at=details.paid_at or datetime.now(UTC),
        external_platform=details.external_platform,
        external_transaction_no=details.external_transaction_no,
        remark=details.remark,
        user_snapshot_name=payable.user_snapshot_name,
        user_snapshot_email=payable.user_snapshot_email,
        company_snapshot_name=payable.company_snapshot_name,
        project_snapshot_name=payable.project_snapshot_name,
        contract_snapshot_ref_no=payable.contract_snapshot_ref_no,
        referral_referred_snapshot_name=payable.referral_referred_snapshot_name,
        referral_referred_snapshot_email=payable.referral_referred_snapshot_email,
        created_by_admin_user_id=admin_user_id,
    )


async def pay_payable(
    *,
    db: AsyncSession,
    payable_id: int,
    details: PayoutDetails,
    admin_user_id: int | None,
) -> tuple[Payment, bool]:
    payable = (
        await db.scalars(select(Payable).where(Payable.id == payable_id).with_for_update())
    ).one_or_none()
    if payable is None:
        raise NotFoundException("Payable not found.")

    existing = (
        await db.scalars(
            select(Payment).where(
                Payment.payable_id == payable.id,
                Payment.entry_type == PaymentEntryType.PAYMENT.value,
            )
        )
    ).one_or_none()
    if existing is not None:
        if payable.status != PayableStatus.PAID.value or not _details_match(existing, details):
            raise ConflictException("Payment already exists with different payout details.")
        return existing, False

    ensure_payable_transition(PayableStatus(payable.status), PayableStatus.PAID)
    if payable.amount <= 0:
        raise ConflictException("Payable amount must be greater than zero.")

    payment = _copy_payable_to_payment(
        payable=payable,
        details=details,
        admin_user_id=admin_user_id,
        entry_type=PaymentEntryType.PAYMENT,
        amount=payable.amount,
    )
    db.add(payment)
    payable.status = PayableStatus.PAID.value
    payable.paid_at = payment.paid_at
    payable.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return payment, True


async def reverse_paid_payment(
    *,
    db: AsyncSession,
    payment_id: int,
    details: PayoutDetails,
    admin_user_id: int | None,
) -> tuple[Payment, bool]:
    payment_ref = await db.get(Payment, payment_id)
    if payment_ref is None or payment_ref.entry_type != PaymentEntryType.PAYMENT.value:
        raise NotFoundException("Payment not found.")

    payable = (
        await db.scalars(select(Payable).where(Payable.id == payment_ref.payable_id).with_for_update())
    ).one_or_none()
    if payable is None:
        raise NotFoundException("Payable not found.")
    original = (
        await db.scalars(select(Payment).where(Payment.id == payment_id).with_for_update())
    ).one_or_none()
    if original is None or original.entry_type != PaymentEntryType.PAYMENT.value:
        raise NotFoundException("Payment not found.")

    existing = (
        await db.scalars(
            select(Payment).where(Payment.reversal_of_payment_id == original.id).with_for_update()
        )
    ).one_or_none()
    if existing is not None:
        if payable.status != PayableStatus.REVERSED.value or not _details_match(existing, details):
            raise ConflictException("Payment was already reversed with different payout details.")
        return existing, False

    ensure_payable_transition(PayableStatus(payable.status), PayableStatus.REVERSED)
    reversal = _copy_payable_to_payment(
        payable=payable,
        details=details,
        admin_user_id=admin_user_id,
        entry_type=PaymentEntryType.REVERSAL,
        amount=-original.amount,
        reversal_of_payment_id=original.id,
    )
    db.add(reversal)
    payable.status = PayableStatus.REVERSED.value
    payable.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return reversal, True
