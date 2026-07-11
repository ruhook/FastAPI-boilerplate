from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .const import PayableStatus
from .model import Payable
from .schema import PayableListPage, PayableListQuery, PayableRead, PayableSummaryRead


def _conditions(query: PayableListQuery) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if query.settlement_month:
        conditions.append(Payable.settlement_month == query.settlement_month)
    if query.payment_type:
        conditions.append(Payable.payment_type == query.payment_type)
    if query.status:
        conditions.append(Payable.status == query.status.value)
    if query.keyword:
        pattern = f"%{query.keyword}%"
        conditions.append(
            or_(
                Payable.source_key.like(pattern),
                Payable.user_snapshot_name.like(pattern),
                Payable.user_snapshot_email.like(pattern),
                Payable.company_snapshot_name.like(pattern),
                Payable.project_snapshot_name.like(pattern),
                Payable.contract_snapshot_ref_no.like(pattern),
            )
        )
    return conditions


async def list_payables(*, db: AsyncSession, query: PayableListQuery) -> PayableListPage:
    conditions = _conditions(query)
    total = int((await db.scalar(select(func.count(Payable.id)).where(*conditions))) or 0)

    rows = (
        await db.scalars(
            select(Payable)
            .where(*conditions)
            .order_by(Payable.created_at.desc(), Payable.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    summary_rows = (
        await db.execute(
            select(
                Payable.status,
                func.count(Payable.id),
                func.coalesce(func.sum(Payable.amount), 0),
            )
            .where(*conditions)
            .group_by(Payable.status)
        )
    ).all()

    counts = {status.value: 0 for status in PayableStatus}
    amounts = {status.value: Decimal("0.00") for status in PayableStatus}
    for status, count, amount in summary_rows:
        counts[str(status)] = int(count)
        amounts[str(status)] = Decimal(amount)

    return PayableListPage(
        items=[PayableRead.model_validate(row) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
        summary=PayableSummaryRead(
            pending_count=counts[PayableStatus.PENDING.value],
            pending_amount=amounts[PayableStatus.PENDING.value],
            processing_count=counts[PayableStatus.PROCESSING.value],
            processing_amount=amounts[PayableStatus.PROCESSING.value],
            paid_count=counts[PayableStatus.PAID.value],
            paid_amount=amounts[PayableStatus.PAID.value],
            cancelled_count=counts[PayableStatus.CANCELLED.value],
            cancelled_amount=amounts[PayableStatus.CANCELLED.value],
            reversed_count=counts[PayableStatus.REVERSED.value],
            reversed_amount=amounts[PayableStatus.REVERSED.value],
            total_count=sum(counts.values()),
            total_amount=sum(amounts.values(), Decimal("0.00")),
        ),
    )
