from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..assets.model import Asset
from ..assets.service import serialize_asset
from ..job.model import Job
from ..user.model import User
from .model import ContractRecord
from .schema import ContractRecordListPage
from .serialization import _extract_id_attachment_asset_id, serialize_contract_record


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
    add_field(
        ["contractReviewStatus", "contract_review_status"],
        "select",
        ContractRecord.contract_review_status,
    )
    add_field(["signingStatus", "signing_status"], "select", ContractRecord.signing_status)

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
    conditions: list[Any] = [
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
        serialize_contract_record(
            record,
            job=job_map[int(record.job_id)],
            company=company_map.get(int(record.id)),
            project=project_map.get(int(record.id)),
            asset_map=asset_map,
            id_attachment_asset_id=id_attachment_asset_ids_by_user.get(int(record.user_id)),
        )
        for record in records
    ]

    return ContractRecordListPage(items=items, total=total, page=page, page_size=page_size).model_dump()
