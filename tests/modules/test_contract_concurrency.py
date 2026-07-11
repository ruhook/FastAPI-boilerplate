from decimal import Decimal

import pytest

from src.app.core.db.database import local_session
from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.contract_record.commands import flush_contract_write
from src.app.modules.contract_record.model import ContractRecord
from tests.modules.test_settlement_sync import _create_salary_source

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_stale_contract_writer_gets_conflict(
    db_session,
    superadmin_credentials: dict[str, str | int],
) -> None:
    timesheet = await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    contract_id = int(timesheet.contract_record_id or 0)
    await db_session.commit()

    async with local_session() as first_session, local_session() as second_session:
        first = await first_session.get(ContractRecord, contract_id)
        second = await second_session.get(ContractRecord, contract_id)
        assert first is not None and second is not None

        first.rate = Decimal("6.00")
        await flush_contract_write(first_session)
        await first_session.commit()

        second.rate = Decimal("7.00")
        with pytest.raises(ConflictException, match="changed by another request"):
            await flush_contract_write(second_session)
