import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.application.contracting import activate_contract
from src.app.modules.contract_record.const import (
    ContractReviewStatus,
    ContractSigningStatus,
    ContractStatus,
)
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job_progress.const import RecruitmentStage
from src.app.modules.job_progress.model import JobProgress
from tests.modules.test_settlement_sync import _create_salary_source

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_activation_advances_job_progress_exactly_once(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    timesheet = await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    contract = await db_session.get(ContractRecord, timesheet.contract_record_id)
    assert contract is not None
    progress = await db_session.get(JobProgress, contract.job_progress_id)
    assert progress is not None
    contract.contract_status = ContractStatus.PENDING_ACTIVATION.value
    contract.contract_review_status = ContractReviewStatus.APPROVED.value
    contract.signing_status = ContractSigningStatus.COMPANY_SEALED.value
    progress.current_stage = RecruitmentStage.CONTRACT_POOL.value
    await db_session.flush()

    first = await activate_contract(
        db=db_session,
        contract_record_id=contract.id,
        admin_user_id=int(superadmin_credentials["id"]),
    )
    first_progress_version = progress.version
    assert first.stage_advanced is True
    assert contract.contract_status == ContractStatus.ACTIVE.value
    assert progress.current_stage == RecruitmentStage.ACTIVE.value

    second = await activate_contract(
        db=db_session,
        contract_record_id=contract.id,
        admin_user_id=int(superadmin_credentials["id"]),
    )
    assert second.stage_advanced is False
    assert progress.version == first_progress_version
