from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..job.model import Job
from ..job_progress.const import RecruitmentStage, get_recruitment_stage_cn_name
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


def _normalize_contract_status_or_400(value: str | None) -> str:
    try:
        return normalize_contract_status(value)
    except ValueError as exc:
        raise BadRequestException("Invalid contract status.") from exc


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def get_default_contract_end_date(effective_date: date | None) -> date | None:
    if effective_date is None:
        return None
    return date(effective_date.year, 12, 31)


async def get_current_contract_record_by_progress_id(
    *,
    progress_id: int,
    db: AsyncSession,
    for_update: bool = False,
) -> ContractRecord | None:
    statement = (
        select(ContractRecord)
        .where(
            ContractRecord.job_progress_id == progress_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .order_by(ContractRecord.version.desc(), ContractRecord.id.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
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


def _extract_id_attachment_asset_id(user_data: dict[str, Any] | None) -> int | None:
    payment_info = (user_data or {}).get("payment_info")
    if not isinstance(payment_info, dict):
        return None
    raw_asset_id = payment_info.get("id_attachment_asset_id")
    if raw_asset_id in (None, "", 0):
        return None
    try:
        return int(raw_asset_id)
    except (TypeError, ValueError):
        return None


def _build_contract_json_text_expression(key: str):
    return func.json_unquote(func.json_extract(ContractRecord.data, f"$.{key}"))


def _build_contract_id_attachment_sql_expression():
    return (
        select(func.json_unquote(func.json_extract(User.data, "$.payment_info.id_attachment_asset_id")))
        .where(
            User.id == ContractRecord.user_id,
            User.is_deleted.is_(False),
        )
        .limit(1)
        .scalar_subquery()
    )


def _build_contract_advanced_filter_field_map() -> dict[str, AdvancedFilterFieldDefinition]:
    field_map: dict[str, AdvancedFilterFieldDefinition] = {}

    def add_field(
        names: list[str],
        filter_kind: str,
        sql_expression: Any,
    ) -> None:
        definition = AdvancedFilterFieldDefinition(
            name=names[0],
            filter_kind=filter_kind,  # type: ignore[arg-type]
            sql_expression=sql_expression,
        )
        for name in names:
            field_map[name] = definition

    add_field(["contractSummary", "contract_summary"], "text", ContractRecord.job_snapshot_title)
    add_field(["agreementRefNo", "agreement_ref_no"], "text", ContractRecord.agreement_ref_no)
    add_field(["contractStatus", "contract_status"], "select", ContractRecord.contract_status)
    add_field(["contractType", "contract_type"], "select", ContractRecord.contract_type)
    add_field(["contractorName", "contractor_name"], "text", ContractRecord.contractor_name)
    add_field(["contractorEmail", "contractor_email"], "email", ContractRecord.user_snapshot_email)
    add_field(["serviceCustomer", "service_customer"], "select", AdminCompany.name)
    add_field(["rate"], "number", ContractRecord.rate)
    add_field(["basePay", "base_pay"], "number", ContractRecord.base_pay)
    add_field(["legalEntity", "legal_entity"], "select", ContractRecord.legal_entity)
    add_field(["workerType", "worker_type"], "select", ContractRecord.worker_type)
    add_field(["effectiveDate", "effective_date"], "date", ContractRecord.effective_date)
    add_field(["endDate", "end_date"], "date", ContractRecord.end_date)
    add_field(["contractAttachment", "contract_attachment"], "file", ContractRecord.contract_attachment_asset_id)
    add_field(["draftContractAttachment", "draft_contract_attachment"], "file", ContractRecord.draft_contract_asset_id)
    add_field(
        ["candidateSignedContractAttachment", "candidate_signed_contract_attachment"],
        "file",
        ContractRecord.candidate_signed_contract_asset_id,
    )
    add_field(
        ["companySealedContractAttachment", "company_sealed_contract_attachment"],
        "file",
        ContractRecord.company_sealed_contract_asset_id,
    )
    add_field(["idAttachment", "id_attachment"], "file", _build_contract_id_attachment_sql_expression())
    add_field(["contractReview", "contract_review"], "select", _build_contract_json_text_expression("contract_review"))
    add_field(["signingStatus", "signing_status"], "select", _build_contract_json_text_expression("signing_status"))

    return field_map


async def _list_id_attachment_asset_ids_by_user(
    *,
    db: AsyncSession,
    user_ids: set[int],
) -> dict[int, int]:
    normalized_user_ids = sorted({user_id for user_id in user_ids if user_id > 0})
    if not normalized_user_ids:
        return {}
    result = await db.execute(
        select(User.id, User.data).where(
            User.id.in_(normalized_user_ids),
            User.is_deleted.is_(False),
        )
    )
    id_attachment_asset_ids: dict[int, int] = {}
    for user_id, user_data in result.all():
        asset_id = _extract_id_attachment_asset_id(user_data)
        if asset_id is not None:
            id_attachment_asset_ids[int(user_id)] = asset_id
    return id_attachment_asset_ids


async def list_contract_records_for_admin(
    *,
    admin_user_id: int,
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    contract_status: str | None = None,
    company_id: int | None = None,
    advanced_filter: str | None = None,
) -> dict[str, Any]:
    advanced_filter_query = parse_advanced_filter_query(advanced_filter)
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
    if has_advanced_filter_rules(advanced_filter_query):
        field_map = _build_contract_advanced_filter_field_map()
        validate_advanced_filter_query(advanced_filter_query, field_map=field_map)
        advanced_filter_condition = build_advanced_filter_query_sql_condition(
            advanced_filter_query,
            field_map=field_map,
        )
        if advanced_filter_condition is not None:
            conditions.append(advanced_filter_condition)

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
    company_map = {int(row[0].id): row[2] for row in rows if row[2] is not None}
    project_map = {int(row[0].id): row[3] for row in rows if row[3] is not None}
    id_attachment_asset_ids_by_user = await _list_id_attachment_asset_ids_by_user(
        db=db,
        user_ids={int(record.user_id) for record in records},
    )

    asset_ids = {
        asset_id
        for record in records
        for asset_id in [
            record.contract_attachment_asset_id,
            record.draft_contract_asset_id,
            record.candidate_signed_contract_asset_id,
            record.company_sealed_contract_asset_id,
            id_attachment_asset_ids_by_user.get(int(record.user_id)),
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
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

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
            service_customer_company_name=company_map[int(record.id)].name if int(record.id) in company_map else None,
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
            base_pay=record.base_pay,
            rate_unit=job_map[int(record.job_id)].compensation_unit if int(record.job_id) in job_map else None,
            legal_entity=record.legal_entity,
            worker_type=record.worker_type,
            effective_date=record.effective_date,
            end_date=record.end_date,
            contract_attachment=_serialize_contract_asset(asset_map.get(int(record.contract_attachment_asset_id or 0))),
            draft_contract_attachment=_serialize_contract_asset(
                asset_map.get(int(record.draft_contract_asset_id or 0))
            ),
            candidate_signed_contract_attachment=_serialize_contract_asset(
                asset_map.get(int(record.candidate_signed_contract_asset_id or 0))
            ),
            company_sealed_contract_attachment=_serialize_contract_asset(
                asset_map.get(int(record.company_sealed_contract_asset_id or 0))
            ),
            id_attachment=_serialize_contract_asset(
                asset_map.get(int(id_attachment_asset_ids_by_user.get(int(record.user_id)) or 0))
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
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Contract record not found.")

    record, job, progress, company, project = row
    previous_effective_date = record.effective_date
    previous_end_date = record.end_date
    updated_fields: list[str] = []
    if contract_status is not None:
        next_contract_status = _normalize_contract_status_or_400(contract_status)
        if next_contract_status == CONTRACT_STATUS_ACTIVE and record.contract_status != CONTRACT_STATUS_ACTIVE:
            raise BadRequestException("Active contracts must be activated by the company signed contract workflow.")
        record.contract_status = next_contract_status
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

    if latest_contract_upload is not None:
        asset_payload = await upload_asset(
            db=db,
            payload=AssetUploadPayload(
                type="contract_attachment",
                module="contract",
                owner_type="admin_user",
                owner_id=admin_user_id,
            ),
            upload=latest_contract_upload,
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
        base_pay=record.base_pay,
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
        id_attachment=_serialize_contract_asset(
            asset_map.get(int(id_attachment_asset_ids_by_user.get(int(record.user_id)) or 0))
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
