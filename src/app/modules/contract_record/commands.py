from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ...application.settlement import sync_contract_rate_change
from ...core.exceptions.http_exceptions import BadRequestException, ConflictException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..job.model import Job
from ..job_progress.const import JobProgressDataKey, RecruitmentStage, get_recruitment_stage_cn_name
from ..job_progress.model import JobProgress
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_PENDING_ACTIVATION,
    CONTRACT_STATUS_TERMINATED,
    CONTRACT_TYPE_NORMAL,
    ContractReviewStatus,
    ContractSigningStatus,
    ContractStatus,
    normalize_contract_type,
)
from .model import ContractRecord
from .policy import (
    ensure_activation_allowed,
    ensure_review_transition,
    ensure_signing_transition,
    ensure_status_transition,
)
from .queries import (
    _list_id_attachment_asset_ids_by_user,
    get_current_contract_record_by_progress_id,
)
from .serialization import (
    _normalize_contract_status_or_400,
    get_default_contract_end_date,
    serialize_contract_record,
)


async def flush_contract_write(db: AsyncSession) -> None:
    try:
        await db.flush()
    except StaleDataError as exc:
        raise ConflictException("Contract record was changed by another request.") from exc


async def get_locked_contract(*, db: AsyncSession, contract_record_id: int) -> ContractRecord:
    contract = (
        await db.scalars(
            select(ContractRecord)
            .where(
                ContractRecord.id == contract_record_id,
                ContractRecord.is_deleted.is_(False),
                ContractRecord.is_current.is_(True),
            )
            .with_for_update()
        )
    ).one_or_none()
    if contract is None:
        raise NotFoundException("Contract record not found.")
    return contract


async def record_draft_upload(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractStatus(contract.contract_status) != ContractStatus.PENDING_ACTIVATION:
        raise ConflictException("Only pending contracts accept a new draft.")
    contract.draft_contract_asset_id = asset_id
    contract.contract_review_status = ContractReviewStatus.PENDING.value
    contract.signing_status = ContractSigningStatus.NOT_SENT.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def mark_contract_sent(
    *,
    db: AsyncSession,
    contract_record_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    current = ContractSigningStatus(contract.signing_status)
    ensure_signing_transition(current, ContractSigningStatus.SENT)
    if contract.draft_contract_asset_id is None:
        raise ConflictException("Contract must have a draft before it can be sent.")
    contract.signing_status = ContractSigningStatus.SENT.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def record_candidate_signature(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    current = ContractSigningStatus(contract.signing_status)
    if current == ContractSigningStatus.CANDIDATE_SIGNED and contract.candidate_signed_contract_asset_id == asset_id:
        return contract
    ensure_signing_transition(current, ContractSigningStatus.CANDIDATE_SIGNED)
    contract.candidate_signed_contract_asset_id = asset_id
    contract.signing_status = ContractSigningStatus.CANDIDATE_SIGNED.value
    contract.contract_review_status = ContractReviewStatus.PENDING.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def review_contract(
    *,
    db: AsyncSession,
    contract_record_id: int,
    target: ContractReviewStatus,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractSigningStatus(contract.signing_status) != ContractSigningStatus.CANDIDATE_SIGNED:
        raise ConflictException("Contract review requires a candidate signature.")
    ensure_review_transition(ContractReviewStatus(contract.contract_review_status), target)
    contract.contract_review_status = target.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def seal_contract(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
    effective_date: date | None = None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractReviewStatus(contract.contract_review_status) != ContractReviewStatus.APPROVED:
        raise ConflictException("Company seal requires review approval.")
    ensure_signing_transition(
        ContractSigningStatus(contract.signing_status),
        ContractSigningStatus.COMPANY_SEALED,
    )
    contract.company_sealed_contract_asset_id = asset_id
    contract.contract_attachment_asset_id = asset_id
    contract.signing_status = ContractSigningStatus.COMPANY_SEALED.value
    contract.effective_date = contract.effective_date or effective_date
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def activate_contract_record(
    *,
    db: AsyncSession,
    contract_record_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    status = ContractStatus(contract.contract_status)
    ensure_activation_allowed(
        contract_status=status,
        review_status=ContractReviewStatus(contract.contract_review_status),
        signing_status=ContractSigningStatus(contract.signing_status),
    )
    contract.contract_status = ContractStatus.ACTIVE.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract


async def close_contract_record(
    *,
    db: AsyncSession,
    contract_record_id: int,
    target: ContractStatus,
    admin_user_id: int | None,
) -> ContractRecord:
    if target not in {ContractStatus.TERMINATED, ContractStatus.EXPIRED}:
        raise ValueError("Contract close target must be terminated or expired.")
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    ensure_status_transition(ContractStatus(contract.contract_status), target)
    contract.contract_status = target.value
    contract.updated_by_admin_user_id = admin_user_id
    await flush_contract_write(db)
    return contract



async def upsert_contract_record_for_progress(
    *,
    progress: JobProgress,
    job: Job,
    db: AsyncSession,
    admin_user_id: int | None = None,
    field_updates: Mapping[str, Any] | None = None,
    data_updates: Mapping[str, Any] | None = None,
) -> ContractRecord:
    current = await get_current_contract_record_by_progress_id(
        progress_id=progress.id,
        db=db,
        for_update=True,
    )

    user_result = await db.execute(select(User).where(User.id == progress.user_id))
    user = user_result.scalar_one_or_none()

    talent = None
    if progress.talent_profile_id is not None:
        talent_result = await db.execute(select(TalentProfile).where(TalentProfile.id == progress.talent_profile_id))
        talent = talent_result.scalar_one_or_none()

    contractor_name = (
        (talent.full_name if talent and talent.full_name else None)
        or (user.name if user else None)
        or (current.contractor_name if current else None)
    )

    if current is None:
        current = ContractRecord(
            user_id=progress.user_id,
            user_snapshot_name=user.name if user else None,
            user_snapshot_email=user.email if user else None,
            talent_profile_id=progress.talent_profile_id,
            application_id=progress.application_id,
            job_id=progress.job_id,
            job_progress_id=progress.id,
            job_snapshot_title=job.title,
            service_customer_company_id=job.company_id,
            service_customer_project_id=job.project_id,
            contractor_name=contractor_name,
            contract_status=CONTRACT_STATUS_PENDING_ACTIVATION,
            contract_type=CONTRACT_TYPE_NORMAL,
            legal_entity="T-Maxx International",
            worker_type="Contractor",
            created_by_admin_user_id=admin_user_id,
            updated_by_admin_user_id=admin_user_id,
            data=dict(data_updates or {}),
        )
        db.add(current)
    else:
        current.user_snapshot_name = current.user_snapshot_name or (user.name if user else None)
        current.user_snapshot_email = current.user_snapshot_email or (user.email if user else None)
        current.talent_profile_id = current.talent_profile_id or progress.talent_profile_id
        current.application_id = current.application_id or progress.application_id
        current.job_id = progress.job_id
        current.job_progress_id = progress.id
        current.job_snapshot_title = job.title
        current.service_customer_company_id = job.company_id
        current.service_customer_project_id = job.project_id
        current.contractor_name = contractor_name
        if not current.legal_entity:
            current.legal_entity = "T-Maxx International"
        if not current.worker_type:
            current.worker_type = "Contractor"
        if admin_user_id is not None:
            current.updated_by_admin_user_id = admin_user_id

    previous_effective_date = current.effective_date
    previous_end_date = current.end_date

    for key, value in (field_updates or {}).items():
        if hasattr(current, key):
            setattr(current, key, value)

    if current.effective_date is not None:
        default_end_date = get_default_contract_end_date(current.effective_date)
        previous_default_end_date = get_default_contract_end_date(previous_effective_date)
        if current.end_date is None:
            current.end_date = default_end_date
        elif (
            "effective_date" in (field_updates or {})
            and "end_date" not in (field_updates or {})
            and previous_end_date == previous_default_end_date
        ):
            current.end_date = default_end_date

    if data_updates:
        current.data = {
            **(current.data or {}),
            **dict(data_updates),
        }

    await flush_contract_write(db)
    return current


async def _sync_progress_after_contract_close(
    *,
    db: AsyncSession,
    record: ContractRecord,
    job: Job,
    progress: JobProgress | None,
    admin_user_id: int,
) -> None:
    if progress is None:
        return
    previous_stage = progress.current_stage
    target_stage: RecruitmentStage | None = None
    if previous_stage == RecruitmentStage.ACTIVE.value:
        target_stage = RecruitmentStage.REPLACED
    elif previous_stage in {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    }:
        target_stage = RecruitmentStage.REJECTED
    if target_stage is None:
        return

    next_progress_data = dict(progress.data or {})
    if target_stage == RecruitmentStage.REPLACED:
        next_progress_data[JobProgressDataKey.REPLACEMENT_REASON.value] = "contract_closed"
    else:
        next_progress_data[JobProgressDataKey.REJECTED_FROM_STAGE.value] = previous_stage
    progress.current_stage = target_stage.value
    progress.entered_stage_at = datetime.now(UTC)
    progress.data = next_progress_data
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
            "to_stage": target_stage.value,
            "to_stage_cn_name": get_recruitment_stage_cn_name(target_stage.value),
            "reason": f"contract_{record.contract_status}",
            "operator_admin_user_id": admin_user_id,
        },
    )


def _apply_admin_contract_status(
    *,
    record: ContractRecord,
    contract_status: str | None,
    admin_user_id: int,
    updated_fields: list[str],
) -> None:
    if contract_status is None:
        return
    next_contract_status = _normalize_contract_status_or_400(contract_status)
    if next_contract_status == CONTRACT_STATUS_ACTIVE and record.contract_status != CONTRACT_STATUS_ACTIVE:
        raise BadRequestException("Active contracts must be activated by the company signed contract workflow.")
    ensure_status_transition(
        ContractStatus(record.contract_status),
        ContractStatus(next_contract_status),
    )
    record.contract_status = next_contract_status
    record.updated_by_admin_user_id = admin_user_id
    updated_fields.append("contract_status")


async def _replace_pending_contract_attachment(
    *,
    db: AsyncSession,
    record: ContractRecord,
    upload: UploadFile | None,
    admin_user_id: int,
    updated_fields: list[str],
) -> None:
    if upload is None:
        return
    if not record.is_current:
        raise BadRequestException("Historical contract attachments cannot be replaced.")
    if record.contract_status != CONTRACT_STATUS_PENDING_ACTIVATION:
        raise BadRequestException(
            "Only pending contract attachments can be replaced; signed or closed contracts must use a new version."
        )
    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="contract_attachment",
            module="contract",
            owner_type="admin_user",
            owner_id=admin_user_id,
        ),
        upload=upload,
    )
    asset_id = int(asset_payload["id"])
    record.contract_attachment_asset_id = asset_id
    record.company_sealed_contract_asset_id = asset_id
    record.updated_by_admin_user_id = admin_user_id
    record.data = {
        **(record.data or {}),
        "contract_attachment_name": asset_payload["original_name"],
        "company_sealed_contract_attachment_name": asset_payload["original_name"],
    }
    updated_fields.append("contract_attachment")


async def update_contract_record_for_admin(
    *,
    contract_record_id: int,
    admin_user_id: int,
    db: AsyncSession,
    contract_status: str | None = None,
    contract_type: str | None = None,
    agreement_ref_no: str | None = None,
    contractor_name: str | None = None,
    rate: Decimal | None = None,
    base_pay: Decimal | None = None,
    legal_entity: str | None = None,
    worker_type: str | None = None,
    effective_date: date | None = None,
    end_date: date | None = None,
    latest_contract_upload: UploadFile | None = None,
    update_contract_type: bool = False,
    update_agreement_ref_no: bool = False,
    update_contractor_name: bool = False,
    update_rate: bool = False,
    update_base_pay: bool = False,
    update_legal_entity: bool = False,
    update_worker_type: bool = False,
    update_effective_date: bool = False,
    update_end_date: bool = False,
) -> dict[str, Any]:
    result = await db.execute(
        select(ContractRecord, Job, JobProgress, AdminCompany, AdminCompanyProject)
        .join(Job, Job.id == ContractRecord.job_id)
        .outerjoin(JobProgress, JobProgress.id == ContractRecord.job_progress_id)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
        .where(
            ContractRecord.id == contract_record_id,
            ContractRecord.is_deleted.is_(False),
            Job.is_deleted.is_(False),
        ).with_for_update()
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Contract record not found.")

    record, job, progress, company, project = row
    previous_effective_date = record.effective_date
    previous_end_date = record.end_date
    updated_fields: list[str] = []
    _apply_admin_contract_status(
        record=record,
        contract_status=contract_status,
        admin_user_id=admin_user_id,
        updated_fields=updated_fields,
    )
    if update_contract_type:
        record.contract_type = normalize_contract_type(contract_type)
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("contract_type")
    if update_agreement_ref_no:
        record.agreement_ref_no = (agreement_ref_no or "").strip() or None
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("agreement_ref_no")
    if update_contractor_name:
        record.contractor_name = (contractor_name or "").strip() or None
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("contractor_name")
    if update_rate:
        record.rate = rate
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("rate")
    if update_base_pay:
        record.base_pay = base_pay
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("base_pay")
    if update_legal_entity:
        record.legal_entity = (legal_entity or "").strip() or "T-Maxx International"
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("legal_entity")
    if update_worker_type:
        record.worker_type = (worker_type or "").strip() or "Contractor"
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("worker_type")
    if update_effective_date:
        record.effective_date = effective_date
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("effective_date")
    if update_end_date:
        record.end_date = end_date
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("end_date")

    if record.effective_date is not None:
        default_end_date = get_default_contract_end_date(record.effective_date)
        previous_default_end_date = get_default_contract_end_date(previous_effective_date)
        if record.end_date is None:
            record.end_date = default_end_date
            record.updated_by_admin_user_id = admin_user_id
            if "end_date" not in updated_fields:
                updated_fields.append("end_date")
        elif update_effective_date and not update_end_date and previous_end_date == previous_default_end_date:
            record.end_date = default_end_date
            record.updated_by_admin_user_id = admin_user_id
            if "end_date" not in updated_fields:
                updated_fields.append("end_date")

    await _replace_pending_contract_attachment(
        db=db,
        record=record,
        upload=latest_contract_upload,
        admin_user_id=admin_user_id,
        updated_fields=updated_fields,
    )

    if contract_status is not None and record.contract_status in {
        ContractStatus.TERMINATED.value,
        ContractStatus.EXPIRED.value,
    }:
        await _sync_progress_after_contract_close(
            db=db,
            record=record,
            job=job,
            progress=progress,
            admin_user_id=admin_user_id,
        )

    await flush_contract_write(db)
    if {"rate", "base_pay", "contract_type", "contract_status"}.intersection(updated_fields):
        await sync_contract_rate_change(db=db, contract_record_id=int(record.id))

    if updated_fields:
        await create_operation_log(
            db=db,
            user_id=record.user_id,
            job_id=record.job_id,
            application_id=record.application_id,
            talent_profile_id=record.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": record.job_progress_id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage if progress is not None else None,
                "current_stage_cn_name": (
                    get_recruitment_stage_cn_name(progress.current_stage) if progress is not None else None
                ),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": updated_fields,
            },
        )

    id_attachment_asset_ids_by_user = await _list_id_attachment_asset_ids_by_user(
        db=db,
        user_ids={int(record.user_id)},
    )
    asset_ids = [
        asset_id
        for asset_id in [
            record.contract_attachment_asset_id,
            record.draft_contract_asset_id,
            record.candidate_signed_contract_asset_id,
            record.company_sealed_contract_asset_id,
            id_attachment_asset_ids_by_user.get(int(record.user_id)),
        ]
        if asset_id not in (None, 0)
    ]
    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(set(asset_ids))),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    return serialize_contract_record(
        record,
        job=job,
        company=company,
        project=project,
        asset_map=asset_map,
        id_attachment_asset_id=id_attachment_asset_ids_by_user.get(int(record.user_id)),
    ).model_dump()


async def resign_contract_record_for_admin(
    *,
    contract_record_id: int,
    admin_user_id: int,
    db: AsyncSession,
    upload: UploadFile,
    contract_status: str | None = None,
    contract_type: str | None = None,
    agreement_ref_no: str | None = None,
    contractor_name: str | None = None,
    rate: Decimal | None = None,
    base_pay: Decimal | None = None,
    legal_entity: str | None = None,
    worker_type: str | None = None,
    effective_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    result = await db.execute(
        select(ContractRecord, Job, JobProgress)
        .join(Job, Job.id == ContractRecord.job_id)
        .outerjoin(JobProgress, JobProgress.id == ContractRecord.job_progress_id)
        .where(
            ContractRecord.id == contract_record_id,
            ContractRecord.is_deleted.is_(False),
            Job.is_deleted.is_(False),
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Contract record not found.")

    old_record, job, progress = row
    if not old_record.is_current:
        raise BadRequestException("Only the current contract can be re-signed.")
    if progress is None or progress.current_stage != RecruitmentStage.ACTIVE.value:
        raise BadRequestException("Only active-stage contracts can be re-signed.")
    if old_record.contract_status != CONTRACT_STATUS_ACTIVE:
        raise BadRequestException("Only active contracts can be re-signed.")
    next_contract_status = _normalize_contract_status_or_400(contract_status or CONTRACT_STATUS_ACTIVE)
    if next_contract_status != CONTRACT_STATUS_ACTIVE:
        raise BadRequestException("Re-signed contracts must be Active.")

    current_result = await db.execute(
        select(ContractRecord).where(
            ContractRecord.job_progress_id == old_record.job_progress_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.id != old_record.id,
        )
    )
    for current_record in current_result.scalars().all():
        if current_record.end_date is None:
            current_record.end_date = get_default_contract_end_date(current_record.effective_date or date.today())
        current_record.is_current = False
        current_record.contract_status = CONTRACT_STATUS_TERMINATED
        current_record.updated_by_admin_user_id = admin_user_id

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="contract_attachment",
            module="contract",
            owner_type="admin_user",
            owner_id=admin_user_id,
        ),
        upload=upload,
    )
    asset_id = int(asset_payload["id"])

    if old_record.end_date is None:
        old_record.end_date = get_default_contract_end_date(old_record.effective_date or date.today())
    old_record.contract_status = CONTRACT_STATUS_TERMINATED
    old_record.is_current = False
    old_record.updated_by_admin_user_id = admin_user_id

    next_effective_date = effective_date or date.today()
    new_record = ContractRecord(
        user_id=old_record.user_id,
        user_snapshot_name=old_record.user_snapshot_name,
        user_snapshot_email=old_record.user_snapshot_email,
        talent_profile_id=old_record.talent_profile_id,
        application_id=old_record.application_id,
        job_id=old_record.job_id,
        job_progress_id=old_record.job_progress_id,
        job_snapshot_title=job.title or old_record.job_snapshot_title,
        previous_contract_record_id=old_record.id,
        service_customer_company_id=old_record.service_customer_company_id,
        service_customer_project_id=old_record.service_customer_project_id,
        agreement_ref_no=(agreement_ref_no or old_record.agreement_ref_no or "").strip() or None,
        contract_status=next_contract_status,
        contract_review_status=ContractReviewStatus.APPROVED.value,
        signing_status=ContractSigningStatus.COMPANY_SEALED.value,
        contract_type=normalize_contract_type(contract_type or old_record.contract_type),
        contractor_name=(contractor_name or old_record.contractor_name or "").strip() or None,
        rate=rate if rate is not None else old_record.rate,
        base_pay=base_pay if base_pay is not None else old_record.base_pay,
        legal_entity=(legal_entity or old_record.legal_entity or "").strip() or "T-Maxx International",
        worker_type=(worker_type or old_record.worker_type or "").strip() or "Contractor",
        effective_date=next_effective_date,
        end_date=end_date if end_date is not None else get_default_contract_end_date(next_effective_date),
        contract_attachment_asset_id=asset_id,
        candidate_signed_contract_asset_id=asset_id,
        company_sealed_contract_asset_id=asset_id,
        parse_status="pending",
        version=int(old_record.version or 1) + 1,
        is_current=True,
        created_by_admin_user_id=admin_user_id,
        updated_by_admin_user_id=admin_user_id,
        data={
            **(old_record.data or {}),
            "resigned_from_contract_record_id": old_record.id,
        },
    )
    db.add(new_record)
    await flush_contract_write(db)

    await create_operation_log(
        db=db,
        user_id=old_record.user_id,
        job_id=old_record.job_id,
        application_id=old_record.application_id,
        talent_profile_id=old_record.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
        data={
            "job_progress_id": old_record.job_progress_id,
            "job_id": job.id,
            "job_title": job.title,
            "current_stage": progress.current_stage if progress is not None else None,
            "current_stage_cn_name": (
                get_recruitment_stage_cn_name(progress.current_stage) if progress is not None else None
            ),
            "operator_admin_user_id": admin_user_id,
            "contract_updated_fields": ["resign_contract"],
            "previous_contract_record_id": old_record.id,
            "new_contract_record_id": new_record.id,
        },
    )

    return await update_contract_record_for_admin(
        contract_record_id=int(new_record.id),
        admin_user_id=admin_user_id,
        db=db,
    )
