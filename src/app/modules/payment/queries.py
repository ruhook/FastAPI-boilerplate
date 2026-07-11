from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .model import Payment
from .schema import (
    CandidatePaymentListPage,
    CandidatePaymentSummaryRead,
    PaymentListPage,
    PaymentListQuery,
    PaymentRead,
)


async def get_payment(*, db: AsyncSession, payment_id: int) -> Payment | None:
    return (await db.scalars(select(Payment).where(Payment.id == payment_id))).one_or_none()


def _month_range(month: str | None) -> tuple[str, datetime, datetime]:
    selected = month or datetime.now(UTC).strftime("%Y-%m")
    year, month_number = (int(part) for part in selected.split("-", 1))
    start = datetime(year, month_number, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month_number == 12
        else datetime(year, month_number + 1, 1, tzinfo=UTC)
    )
    return selected, start, end


def _conditions(query: PaymentListQuery) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if query.payment_type:
        conditions.append(Payment.payment_type == query.payment_type)
    if query.user_id:
        conditions.append(Payment.user_id == query.user_id)
    if query.month:
        _selected, start, end = _month_range(query.month)
        conditions.extend((Payment.paid_at >= start, Payment.paid_at < end))
    if query.keyword:
        pattern = f"%{query.keyword}%"
        conditions.append(
            or_(
                Payment.user_snapshot_name.like(pattern),
                Payment.user_snapshot_email.like(pattern),
                Payment.external_transaction_no.like(pattern),
                Payment.company_snapshot_name.like(pattern),
                Payment.project_snapshot_name.like(pattern),
                Payment.contract_snapshot_ref_no.like(pattern),
            )
        )
    return conditions


async def list_payments(*, db: AsyncSession, query: PaymentListQuery) -> PaymentListPage:
    conditions = _conditions(query)
    total = int((await db.scalar(select(func.count(Payment.id)).where(*conditions))) or 0)
    records = (
        await db.scalars(
            select(Payment)
            .where(*conditions)
            .order_by(Payment.paid_at.desc(), Payment.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return PaymentListPage(
        items=[PaymentRead.model_validate(record) for record in records],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def list_candidate_payments(
    *,
    db: AsyncSession,
    user_id: int,
    query: PaymentListQuery,
) -> CandidatePaymentListPage:
    selected_month, month_start, month_end = _month_range(query.month)
    list_query = query.model_copy(update={"user_id": user_id})
    page = await list_payments(db=db, query=list_query)
    base = [Payment.user_id == user_id]
    total_paid = Decimal((await db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(*base))) or 0)
    month_paid = Decimal(
        (
            await db.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    *base,
                    Payment.paid_at >= month_start,
                    Payment.paid_at < month_end,
                )
            )
        )
        or 0
    )
    referral_paid = Decimal(
        (
            await db.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    *base,
                    Payment.payment_type == "referral_reward",
                )
            )
        )
        or 0
    )
    latest_payment_at = await db.scalar(select(func.max(Payment.paid_at)).where(*base))
    return CandidatePaymentListPage(
        **page.model_dump(),
        summary=CandidatePaymentSummaryRead(
            total_paid=total_paid,
            month_paid=month_paid,
            referral_rewards_paid=referral_paid,
            latest_payment_at=latest_payment_at,
            month=selected_month,
        ),
    )
