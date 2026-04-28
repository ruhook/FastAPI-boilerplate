from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..job.model import Job
from ..job_progress.const import get_recruitment_stage_cn_name
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
    normalize_contract_status,
    normalize_contract_type,
)
from .model import ContractRecord
from .schema import ContractRecordAssetRead, ContractRecordListItemRead, ContractRecordListPage


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


async def get_current_contract_record_by_progress_id(
    *,
    progress_id: int,
    db: AsyncSession,
) -> ContractRecord | None:
    result = await db.execute(
        select(ContractRecord)
        .where(
            ContractRecord.job_progress_id == progress_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .order_by(ContractRecord.version.desc(), ContractRecord.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_current_contract_records_by_progress_ids(
    *,
    progress_ids: list[int],
    db: AsyncSession,
) -> dict[int, ContractRecord]:
    normalized_ids = sorted({progress_id for progress_id in progress_ids if progress_id > 0})
    if not normalized_ids:
        return {}

    result = await db.execute(
        select(ContractRecord)
        .where(
            ContractRecord.job_progress_id.in_(normalized_ids),
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .order_by(
            ContractRecord.job_progress_id.asc(),
            ContractRecord.version.desc(),
            ContractRecord.id.desc(),
        )
    )

    records: dict[int, ContractRecord] = {}
    for record in result.scalars().all():
        records.setdefault(int(record.job_progress_id), record)
    return records


async def upsert_contract_record_for_progress(
    *,
    progress: JobProgress,
    job: Job,
    db: AsyncSession,
    admin_user_id: int | None = None,
    field_updates: Mapping[str, Any] | None = None,
    data_updates: Mapping[str, Any] | None = None,
) -> ContractRecord:
    current = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)

    user_result = await db.execute(select(User).where(User.id == progress.user_id))
    user = user_result.scalar_one_or_none()

    talent = None
    if progress.talent_profile_id is not None:
        talent_result = await db.execute(
            select(TalentProfile).where(TalentProfile.id == progress.talent_profile_id)
        )
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
        if not current.contract_status or current.contract_status in {
            "draft_uploaded",
            "candidate_signed_uploaded",
            "company_sealed_uploaded",
        }:
            current.contract_status = CONTRACT_STATUS_PENDING_ACTIVATION
        if not current.legal_entity:
            current.legal_entity = "T-Maxx International"
        if not current.worker_type:
            current.worker_type = "Contractor"
        if admin_user_id is not None:
            current.updated_by_admin_user_id = admin_user_id

    for key, value in (field_updates or {}).items():
        if hasattr(current, key):
            setattr(current, key, value)

    if data_updates:
        current.data = {
            **(current.data or {}),
            **dict(data_updates),
        }

    await db.flush()
    return current


def _serialize_contract_asset(asset_payload: dict[str, Any] | None) -> ContractRecordAssetRead | None:
    if not asset_payload:
        return None
    return ContractRecordAssetRead(
        asset_id=int(asset_payload["id"]),
        name=str(asset_payload["original_name"]),
        preview_url=asset_payload.get("preview_url"),
        download_url=asset_payload.get("download_url"),
        mime_type=asset_payload.get("mime_type"),
    )


async def list_contract_records_for_admin(
    *,
    admin_user_id: int,
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    contract_status: str | None = None,
    company_id: int | None = None,
) -> dict[str, Any]:
    conditions = [
        ContractRecord.is_deleted.is_(False),
        Job.is_deleted.is_(False),
    ]

    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                ContractRecord.agreement_ref_no.ilike(term),
                ContractRecord.contractor_name.ilike(term),
                ContractRecord.user_snapshot_email.ilike(term),
                AdminCompany.name.ilike(term),
                ContractRecord.job_snapshot_title.ilike(term),
            )
        )
    if contract_status:
        conditions.append(ContractRecord.contract_status == contract_status)
    if company_id is not None:
        conditions.append(ContractRecord.service_customer_company_id == company_id)

    total_result = await db.execute(
        select(func.count())
        .select_from(ContractRecord)
        .join(Job, Job.id == ContractRecord.job_id)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        select(ContractRecord, Job, AdminCompany, AdminCompanyProject)
        .join(Job, Job.id == ContractRecord.job_id)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
        .where(*conditions)
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()
    records = [row[0] for row in rows]
    job_map = {int(row[1].id): row[1] for row in rows}
    company_map = {
        int(row[0].id): row[2]
        for row in rows
        if row[2] is not None
    }
    project_map = {
        int(row[0].id): row[3]
        for row in rows
        if row[3] is not None
    }

    asset_ids = {
        asset_id
        for record in records
        for asset_id in [
            record.contract_attachment_asset_id,
            record.draft_contract_asset_id,
            record.candidate_signed_contract_asset_id,
            record.company_sealed_contract_asset_id,
        ]
        if asset_id not in (None, 0)
    }

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {
            int(asset.id): serialize_asset(asset)
            for asset in asset_result.scalars().all()
        }

    items = [
        ContractRecordListItemRead(
            id=record.id,
            previous_contract_record_id=record.previous_contract_record_id,
            version=record.version,
            is_current=record.is_current,
            user_id=record.user_id,
            talent_profile_id=record.talent_profile_id,
            application_id=record.application_id,
            job_id=record.job_id,
            job_progress_id=record.job_progress_id,
            job_title=record.job_snapshot_title,
            service_customer_company_id=record.service_customer_company_id,
            service_customer_company_name=company_map[int(record.id)].name
            if int(record.id) in company_map
            else None,
            service_customer_project_id=record.service_customer_project_id,
            service_customer_project_name=project_map.get(int(record.id)).name
            if project_map.get(int(record.id)) is not None
            else None,
            agreement_ref_no=record.agreement_ref_no,
            contract_status=record.contract_status,
            contract_type=record.contract_type,
            contractor_name=record.contractor_name,
            contractor_email=record.user_snapshot_email,
            rate=record.rate,
            rate_unit=job_map[int(record.job_id)].compensation_unit if int(record.job_id) in job_map else None,
            legal_entity=record.legal_entity,
            worker_type=record.worker_type,
            effective_date=record.effective_date,
            end_date=record.end_date,
            contract_attachment=_serialize_contract_asset(asset_map.get(int(record.contract_attachment_asset_id or 0))),
            draft_contract_attachment=_serialize_contract_asset(asset_map.get(int(record.draft_contract_asset_id or 0))),
            candidate_signed_contract_attachment=_serialize_contract_asset(
                asset_map.get(int(record.candidate_signed_contract_asset_id or 0))
            ),
            company_sealed_contract_attachment=_serialize_contract_asset(
                asset_map.get(int(record.company_sealed_contract_asset_id or 0))
            ),
            contract_review=(record.data or {}).get("contract_review"),
            signing_status=(record.data or {}).get("signing_status"),
            created_at=record.created_at,
            updated_at=record.updated_at,
        ).model_dump()
        for record in records
    ]

    return ContractRecordListPage(items=items, total=total, page=page, page_size=page_size).model_dump()


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
    legal_entity: str | None = None,
    worker_type: str | None = None,
    effective_date: date | None = None,
    end_date: date | None = None,
    update_contract_type: bool = False,
    update_agreement_ref_no: bool = False,
    update_contractor_name: bool = False,
    update_rate: bool = False,
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
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Contract record not found.")

    record, job, progress, company, project = row
    updated_fields: list[str] = []
    if contract_status is not None:
        record.contract_status = normalize_contract_status(contract_status)
        record.updated_by_admin_user_id = admin_user_id
        updated_fields.append("contract_status")
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

    await db.flush()

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

    asset_ids = [
        asset_id
        for asset_id in [
            record.contract_attachment_asset_id,
            record.draft_contract_asset_id,
            record.candidate_signed_contract_asset_id,
            record.company_sealed_contract_asset_id,
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
        asset_map = {
            int(asset.id): serialize_asset(asset)
            for asset in asset_result.scalars().all()
        }

    return ContractRecordListItemRead(
        id=record.id,
        previous_contract_record_id=record.previous_contract_record_id,
        version=record.version,
        is_current=record.is_current,
        user_id=record.user_id,
        talent_profile_id=record.talent_profile_id,
        application_id=record.application_id,
        job_id=record.job_id,
        job_progress_id=record.job_progress_id,
        job_title=record.job_snapshot_title,
        service_customer_company_id=record.service_customer_company_id,
        service_customer_company_name=company.name if company is not None else None,
        service_customer_project_id=record.service_customer_project_id,
        service_customer_project_name=project.name if project is not None else None,
        agreement_ref_no=record.agreement_ref_no,
        contract_status=record.contract_status,
        contract_type=record.contract_type,
        contractor_name=record.contractor_name,
        contractor_email=record.user_snapshot_email,
        rate=record.rate,
        rate_unit=job.compensation_unit,
        legal_entity=record.legal_entity,
        worker_type=record.worker_type,
        effective_date=record.effective_date,
        end_date=record.end_date,
        contract_attachment=_serialize_contract_asset(asset_map.get(int(record.contract_attachment_asset_id or 0))),
        draft_contract_attachment=_serialize_contract_asset(asset_map.get(int(record.draft_contract_asset_id or 0))),
        candidate_signed_contract_attachment=_serialize_contract_asset(
            asset_map.get(int(record.candidate_signed_contract_asset_id or 0))
        ),
        company_sealed_contract_attachment=_serialize_contract_asset(
            asset_map.get(int(record.company_sealed_contract_asset_id or 0))
        ),
        contract_review=(record.data or {}).get("contract_review"),
        signing_status=(record.data or {}).get("signing_status"),
        created_at=record.created_at,
        updated_at=record.updated_at,
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

    current_result = await db.execute(
        select(ContractRecord).where(
            ContractRecord.job_progress_id == old_record.job_progress_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.id != old_record.id,
        )
    )
    for current_record in current_result.scalars().all():
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

    old_record.contract_status = CONTRACT_STATUS_TERMINATED
    old_record.is_current = False
    old_record.updated_by_admin_user_id = admin_user_id

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
        contract_status=normalize_contract_status(contract_status or CONTRACT_STATUS_ACTIVE),
        contract_type=normalize_contract_type(contract_type or old_record.contract_type),
        contractor_name=(contractor_name or old_record.contractor_name or "").strip() or None,
        rate=rate if rate is not None else old_record.rate,
        legal_entity=(legal_entity or old_record.legal_entity or "").strip() or "T-Maxx International",
        worker_type=(worker_type or old_record.worker_type or "").strip() or "Contractor",
        effective_date=effective_date or date.today(),
        end_date=end_date,
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
            "contract_review": "审核通过",
            "resigned_from_contract_record_id": old_record.id,
        },
    )
    db.add(new_record)
    await db.flush()

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
