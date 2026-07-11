from datetime import UTC, date, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.contracting import activate_contract
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..assets.model import Asset
from ..assets.schema import AssetRead, AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..candidate_application.model import CandidateApplication
from ..candidate_internal_notification.service import create_candidate_internal_notification
from ..contract_record.commands import upsert_contract_record_for_progress
from ..contract_record.const import (
    CONTRACT_STATUS_EXPIRED,
    CONTRACT_STATUS_TERMINATED,
    ContractReviewStatus,
    ContractSigningStatus,
)
from ..contract_record.model import ContractRecord
from ..contract_record.queries import get_current_contract_record_by_progress_id
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from .const import (
    RecruitmentStage,
    get_recruitment_stage_cn_name,
)
from .model import JobProgress
from .normalization import (
    _normalize_decimal,
)
from .schema import (
    JobProgressCandidateSignedContractUploadResponse,
    JobProgressCompanySealedContractUploadResponse,
    JobProgressContractDraftUploadResponse,
    JobProgressContractRecordUpdateItemRead,
    JobProgressContractRecordUpdateResponse,
)
from .serialization import (
    _extract_contract_record_asset_ids,
    _serialize_contract_record_data,
    _serialize_process_assets,
    _serialize_process_data,
)
from .state import get_job_progress_models

CONTRACT_RECORD_FIELD_STAGE_MAP: dict[str, set[str]] = {
    "agreement_ref_no": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "rate": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "end_date": {
        RecruitmentStage.CONTRACT_POOL.value,
    },
}


def _validate_contract_record_update_stage(*, stage: str, changed_fields: list[str]) -> None:
    unsupported_fields = sorted(
        {field for field in changed_fields if stage not in CONTRACT_RECORD_FIELD_STAGE_MAP.get(field, set())}
    )
    if unsupported_fields:
        stage_name = get_recruitment_stage_cn_name(stage)
        raise BadRequestException(f"Contract fields {', '.join(unsupported_fields)} cannot be updated in {stage_name}.")


async def update_job_progress_contract_record(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    ensure_contract_record: bool = False,
    agreement_ref_no: str | None = None,
    rate: str | None = None,
    end_date: date | None = None,
    update_agreement_ref_no: bool = False,
    update_rate: bool = False,
    update_end_date: bool = False,
) -> dict[str, Any]:
    changed_fields: list[str] = []
    field_updates: dict[str, Any] = {}
    has_agreement_ref_no_update = update_agreement_ref_no or agreement_ref_no is not None
    has_rate_update = update_rate or rate is not None
    has_end_date_update = update_end_date or end_date is not None

    if has_agreement_ref_no_update:
        field_updates["agreement_ref_no"] = (agreement_ref_no or "").strip() or None
        changed_fields.append("agreement_ref_no")
    if has_rate_update:
        field_updates["rate"] = _normalize_decimal(rate)
        changed_fields.append("rate")
    if has_end_date_update:
        field_updates["end_date"] = end_date
        changed_fields.append("end_date")

    if not changed_fields and not ensure_contract_record:
        raise BadRequestException("At least one contract field is required.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    updated_contract_records: dict[int, ContractRecord] = {}
    for progress in progress_items:
        if ensure_contract_record and progress.current_stage not in {
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.CONTRACT_POOL.value,
        }:
            raise BadRequestException("Contract record can only be supplemented in 筛选通过 or 合同库.")
        _validate_contract_record_update_stage(
            stage=progress.current_stage,
            changed_fields=changed_fields,
        )
        contract_record = await upsert_contract_record_for_progress(
            progress=progress,
            job=job,
            db=db,
            admin_user_id=admin_user_id,
            field_updates=field_updates,
        )
        updated_contract_records[progress.id] = contract_record
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": changed_fields,
                "contract_record_ensured": ensure_contract_record,
            },
        )

    asset_ids: set[int] = set()
    for record in updated_contract_records.values():
        asset_ids.update(_extract_contract_record_asset_ids(record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    await db.flush()
    return JobProgressContractRecordUpdateResponse(
        updated_count=len(progress_items),
        updated_field_keys=changed_fields,
        items=[
            JobProgressContractRecordUpdateItemRead(
                progress_id=progress.id,
                contract_record_data=_serialize_contract_record_data(
                    progress=progress,
                    contract_record=updated_contract_records.get(progress.id),
                    asset_map=asset_map,
                ),
            )
            for progress in progress_items
        ],
    ).model_dump()


async def submit_job_progress_candidate_signed_contract(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.user_id == user_id,
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.SCREENING_PASSED.value,
                    RecruitmentStage.CONTRACT_POOL.value,
                ]
            ),
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Signed contract upload record not found for this job.")

    file_name = (upload.filename or "").strip().lower()
    if not file_name.endswith((".doc", ".docx")):
        raise BadRequestException("Signed contract must be uploaded as a .doc or .docx file.")

    progress_data = dict(progress.data or {})
    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db, for_update=True)
    if contract_record is None or contract_record.draft_contract_asset_id in (None, "", 0):
        raise BadRequestException("Draft contract is not available yet.")
    if contract_record.contract_status in {CONTRACT_STATUS_TERMINATED, CONTRACT_STATUS_EXPIRED}:
        raise BadRequestException("Contract signing is no longer available because this contract is inactive.")

    if (
        progress.current_stage == RecruitmentStage.CONTRACT_POOL.value
        and contract_record.candidate_signed_contract_asset_id not in (None, "", 0)
        and contract_record.contract_review_status != ContractReviewStatus.CHANGES_REQUESTED.value
    ):
        raise BadRequestException(
            "Your signed contract is currently under review. "
            "You can upload a new version after the review status changes to Needs Revision."
        )

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="user",
            owner_id=user_id,
        ),
        upload=upload,
    )

    submitted_at = datetime.now(UTC)
    from_stage = progress.current_stage
    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "submitted_contract_asset_id": int(asset_payload["id"]),
            "submitted_contract_attachment": asset_payload["original_name"],
            "submitted_contract_at": submitted_at.isoformat(),
        },
    )

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        field_updates={
            "candidate_signed_contract_asset_id": int(asset_payload["id"]),
            "signing_status": ContractSigningStatus.CANDIDATE_SIGNED.value,
            "contract_review_status": ContractReviewStatus.PENDING.value,
            "parse_status": "pending",
            "parse_error": None,
        },
        data_updates={
            "source": "single_signed_upload",
            "candidate_signed_contract_attachment_name": asset_payload["original_name"],
            "candidate_signed_contract_submitted_at": submitted_at.isoformat(),
        },
    )

    if from_stage == RecruitmentStage.SCREENING_PASSED.value:
        progress.current_stage = RecruitmentStage.CONTRACT_POOL.value
        progress.entered_stage_at = submitted_at
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
                "from_stage": from_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(from_stage),
                "to_stage": RecruitmentStage.CONTRACT_POOL.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.CONTRACT_POOL.value),
                "reason": "candidate_signed_contract_submitted",
            },
        )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCandidateSignedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        candidate_signed_contract_asset=AssetRead.model_validate(asset_payload),
        process_data=_serialize_process_data(progress_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()


async def upload_job_progress_contract_draft(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress)
        .where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    }:
        raise BadRequestException("Contract draft can only be uploaded in 筛选通过 or 合同库.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )

    current_process_data = dict(progress.data or {})

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "contract_draft_asset_id": int(asset_payload["id"]),
            "contract_draft_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )

    uploaded_at = datetime.now(UTC)
    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates={
            "draft_contract_asset_id": int(asset_payload["id"]),
            "signing_status": ContractSigningStatus.NOT_SENT.value,
            "contract_review_status": ContractReviewStatus.PENDING.value,
            "effective_date": uploaded_at.date(),
        },
        data_updates={
            "source": "single_draft_upload",
            "draft_contract_attachment_name": asset_payload["original_name"],
            "draft_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressContractDraftUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        contract_draft_asset=AssetRead.model_validate(asset_payload),
        process_data=_serialize_process_data(current_process_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(current_process_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()


async def upload_job_progress_company_sealed_contract(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress)
        .where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.CONTRACT_POOL.value,
        RecruitmentStage.ACTIVE.value,
    }:
        raise BadRequestException("Company signed contract can only be uploaded in 合同库 or Active.")

    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db, for_update=True)
    if contract_record is None:
        raise BadRequestException("Company signed contract requires a contract record.")
    if contract_record.candidate_signed_contract_asset_id in (None, 0, ""):
        raise BadRequestException(
            "Company signed contract can only be uploaded after the candidate signed contract is submitted."
        )

    if contract_record.contract_review_status != ContractReviewStatus.APPROVED.value:
        raise BadRequestException("Company signed contract can only be uploaded after contract review is approved.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )
    uploaded_at = datetime.now(UTC)

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )

    field_updates: dict[str, Any] = {
        "company_sealed_contract_asset_id": int(asset_payload["id"]),
        "contract_attachment_asset_id": int(asset_payload["id"]),
        "signing_status": ContractSigningStatus.COMPANY_SEALED.value,
    }
    if contract_record.effective_date is None:
        field_updates["effective_date"] = uploaded_at.date()

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates=field_updates,
        data_updates={
            "source": "single_company_sealed_upload",
            "company_sealed_contract_attachment_name": asset_payload["original_name"],
            "company_sealed_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )
    await activate_contract(
        db=db,
        contract_record_id=int(contract_record.id),
        admin_user_id=admin_user_id,
    )
    await create_candidate_internal_notification(
        db=db,
        recipient_user_id=progress.user_id,
        sender_admin_user_id=admin_user_id,
        category="contract_company_signed",
        title="Your contract is ready",
        description=f"The company countersigned contract for {job.title} is ready. You can view it in My Contracts.",
        action_url=f"/my-jobs/{progress.application_id}",
        data={
            "job_id": job.id,
            "job_title": job.title,
            "job_progress_id": progress.id,
            "application_id": progress.application_id,
            "contract_record_id": contract_record.id,
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
        },
    )
    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCompanySealedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        company_sealed_contract_asset=AssetRead.model_validate(asset_payload),
        process_data=_serialize_process_data(progress.data or {}, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress.data or {}, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()
