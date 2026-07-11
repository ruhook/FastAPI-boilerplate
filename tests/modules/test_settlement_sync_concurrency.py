import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.application.settlement import sync_settlement_month
from src.app.core.db.database import local_session
from src.app.modules.payable.model import Payable, PayableTimesheetSource
from tests.modules.test_settlement_sync import _create_salary_source

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_two_sessions_materialize_one_payable_per_source_key(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    await db_session.commit()

    async def sync_once() -> None:
        async with local_session() as session:
            await sync_settlement_month(db=session, settlement_month="2026-07")
            await session.commit()

    await asyncio.gather(sync_once(), sync_once())

    await db_session.rollback()
    payable_count = await db_session.scalar(select(func.count()).select_from(Payable))
    source_count = await db_session.scalar(select(func.count()).select_from(PayableTimesheetSource))
    assert payable_count == 1
    assert source_count == 1
