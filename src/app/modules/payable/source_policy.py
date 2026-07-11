from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ConflictException
from .const import PayableStatus
from .model import Payable, PayableTimesheetSource


async def ensure_timesheets_editable(db: AsyncSession, record_ids: Sequence[int]) -> None:
    normalized_ids = {int(record_id) for record_id in record_ids}
    if not normalized_ids:
        return

    result = await db.execute(
        select(PayableTimesheetSource.id)
        .join(Payable, Payable.id == PayableTimesheetSource.payable_id)
        .where(
            PayableTimesheetSource.project_timesheet_record_id.in_(normalized_ids),
            Payable.status.in_(
                (
                    PayableStatus.PROCESSING.value,
                    PayableStatus.PAID.value,
                    PayableStatus.REVERSED.value,
                )
            ),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise ConflictException("Timesheet record is locked by settlement.")
