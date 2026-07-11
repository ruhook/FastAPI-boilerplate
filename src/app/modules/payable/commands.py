from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ConflictException, NotFoundException
from .const import PayableStatus
from .model import Payable
from .policy import ensure_payable_transition
from .schema import ManualPayableCreateRequest, PayableDraft
from .source_keys import manual_source_key


def _apply_draft(payable: Payable, draft: PayableDraft) -> None:
    payable.payment_type = draft.payment_type
    payable.settlement_month = draft.settlement_month
    payable.user_id = draft.user_id
    payable.talent_profile_id = draft.talent_profile_id
    payable.contract_record_id = draft.contract_record_id
    payable.referral_record_id = draft.referral_record_id
    payable.company_id = draft.company_id
    payable.project_id = draft.project_id
    payable.amount = draft.amount
    payable.currency = draft.currency
    payable.calculation_snapshot = dict(draft.calculation_snapshot)
    payable.user_snapshot_name = draft.user_snapshot_name
    payable.user_snapshot_email = draft.user_snapshot_email
    payable.company_snapshot_name = draft.company_snapshot_name
    payable.project_snapshot_name = draft.project_snapshot_name
    payable.contract_snapshot_ref_no = draft.contract_snapshot_ref_no
    payable.referral_referred_user_id = draft.referral_referred_user_id
    payable.referral_referred_snapshot_name = draft.referral_referred_snapshot_name
    payable.referral_referred_snapshot_email = draft.referral_referred_snapshot_email


def _update_pending_payable(payable: Payable, draft: PayableDraft) -> Payable:
    if payable.status != PayableStatus.PENDING.value:
        raise ConflictException("Payable can no longer be recalculated after processing has started.")
    _apply_draft(payable, draft)
    return payable


async def upsert_pending_payable(*, db: AsyncSession, draft: PayableDraft) -> Payable:
    existing = (
        await db.scalars(select(Payable).where(Payable.source_key == draft.source_key).with_for_update())
    ).one_or_none()
    if existing is not None:
        _update_pending_payable(existing, draft)
        await db.flush()
        return existing

    payable = Payable(source_key=draft.source_key)
    _apply_draft(payable, draft)
    try:
        async with db.begin_nested():
            db.add(payable)
            await db.flush()
    except IntegrityError:
        conflicting = (
            await db.scalars(select(Payable).where(Payable.source_key == draft.source_key).with_for_update())
        ).one_or_none()
        if conflicting is None:
            raise
        _update_pending_payable(conflicting, draft)
        await db.flush()
        return conflicting
    return payable


async def create_manual_payable(
    *,
    db: AsyncSession,
    payload: ManualPayableCreateRequest,
    admin_user_id: int | None,
) -> Payable:
    draft = PayableDraft(
        source_key=manual_source_key(),
        payment_type=payload.payment_type,
        settlement_month=payload.settlement_month,
        user_id=payload.user_id,
        amount=payload.amount,
        currency=payload.currency,
        calculation_snapshot={"remark": payload.remark} if payload.remark else {},
        talent_profile_id=payload.talent_profile_id,
        contract_record_id=payload.contract_record_id,
        referral_record_id=payload.referral_record_id,
        company_id=payload.company_id,
        project_id=payload.project_id,
    )
    payable = Payable(source_key=draft.source_key, created_by_admin_user_id=admin_user_id)
    _apply_draft(payable, draft)
    db.add(payable)
    await db.flush()
    await db.refresh(payable)
    return payable


async def transition_payables(
    *,
    db: AsyncSession,
    payable_ids: list[int],
    target: PayableStatus,
    admin_user_id: int | None,
) -> list[Payable]:
    ordered_ids = sorted(set(payable_ids))
    if not ordered_ids:
        return []
    if target in {PayableStatus.PAID, PayableStatus.REVERSED}:
        raise ConflictException("Paid and reversed states can only be created by payment commands.")

    payables = list(
        (
            await db.scalars(
                select(Payable)
                .where(Payable.id.in_(ordered_ids))
                .order_by(Payable.id.asc())
                .with_for_update()
            )
        ).all()
    )
    if len(payables) != len(ordered_ids):
        raise NotFoundException("Payable not found.")

    now = datetime.now(UTC)
    for payable in payables:
        ensure_payable_transition(PayableStatus(payable.status), target)
        payable.status = target.value
        payable.updated_by_admin_user_id = admin_user_id
        if target == PayableStatus.PROCESSING:
            payable.processing_started_at = now
        elif target == PayableStatus.PENDING:
            payable.processing_started_at = None
            payable.cancelled_at = None
        elif target == PayableStatus.CANCELLED:
            payable.cancelled_at = now
    await db.flush()
    for payable in payables:
        await db.refresh(payable)
    return payables
