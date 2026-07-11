from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions.http_exceptions import NotFoundException
from ..modules.contract_record.commands import activate_contract_record
from ..modules.contract_record.model import ContractRecord
from ..modules.job.model import Job
from ..modules.job_progress.const import RecruitmentStage, get_recruitment_stage_cn_name
from ..modules.job_progress.model import JobProgress
from ..modules.operation_log.const import OperationLogType
from ..modules.operation_log.service import create_operation_log
from ..modules.referral_bonus_model.service import ensure_user_referral_profile_from_job


@dataclass(frozen=True, slots=True)
class ContractActivationResult:
    contract: ContractRecord
    stage_advanced: bool


async def activate_contract(
    *,
    db: AsyncSession,
    contract_record_id: int,
    admin_user_id: int | None,
) -> ContractActivationResult:
    contract = await activate_contract_record(
        db=db,
        contract_record_id=contract_record_id,
        admin_user_id=admin_user_id,
    )
    progress = (
        await db.scalars(
            select(JobProgress).where(JobProgress.id == contract.job_progress_id).with_for_update()
        )
    ).one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    job = await db.get(Job, contract.job_id)
    if job is None or job.is_deleted:
        raise NotFoundException("Job not found.")

    previous_stage = progress.current_stage
    stage_advanced = previous_stage != RecruitmentStage.ACTIVE.value
    if stage_advanced:
        progress.current_stage = RecruitmentStage.ACTIVE.value
        progress.entered_stage_at = datetime.now(UTC)
    await ensure_user_referral_profile_from_job(
        user_id=int(contract.user_id),
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        contract_record=contract,
    )
    if stage_advanced:
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": previous_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(previous_stage),
                "to_stage": RecruitmentStage.ACTIVE.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ACTIVE.value),
                "reason": "contract_activated",
                "operator_admin_user_id": admin_user_id,
            },
        )
    await db.flush()
    return ContractActivationResult(contract=contract, stage_advanced=stage_advanced)
