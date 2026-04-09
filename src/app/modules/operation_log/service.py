from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .model import OperationLog


async def create_operation_log(
    *,
    db: AsyncSession,
    user_id: int | None,
    job_id: int | None = None,
    application_id: int | None = None,
    talent_profile_id: int | None = None,
    log_type: str,
    data: dict[str, Any] | None = None,
) -> OperationLog:
    log = OperationLog(
        user_id=user_id,
        job_id=job_id,
        application_id=application_id,
        talent_profile_id=talent_profile_id,
        log_type=log_type,
        data=data or {},
    )
    db.add(log)
    await db.flush()
    return log
