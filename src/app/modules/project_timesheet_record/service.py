import re
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.company.service import (
    COMPANY_DATA_TIMESHEET_LANGUAGES_KEY,
    COMPANY_DATA_TIMESHEET_ROLES_KEY,
    COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY,
)
from ..assets.model import Asset
from ..assets.service import ensure_assets_exist, serialize_asset
from ..contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_TYPE_TEAM_LEADER,
    INACTIVE_CONTRACT_STATUSES,
)
from ..contract_record.model import ContractRecord
from ..job.const import JOB_DATA_LANGUAGES_KEY
from ..job.model import Job
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .model import ProjectTimesheetRecord
from .schema import (
    CandidateTimesheetContractRead,
    CandidateTimesheetDashboardRead,
    CandidateTimesheetEntryRead,
    CandidateTimesheetReferralRewardRead,
    CandidateTimesheetTeamLeaderBonusRead,
    CandidateTimesheetWorkspaceRead,
    ProjectTimesheetAnalyticsFilterOptionRead,
    ProjectTimesheetAnalyticsMetricItemRead,
    ProjectTimesheetAnalyticsRead,
    ProjectTimesheetAnalyticsSummaryRead,
    ProjectTimesheetAnalyticsTrendItemRead,
    ProjectTimesheetBatchCreateRequest,
    ProjectTimesheetBatchCreateResponse,
    ProjectTimesheetBatchDeleteRequest,
    ProjectTimesheetBatchDeleteResponse,
    ProjectTimesheetDashboardItemRead,
    ProjectTimesheetNoteAssetRead,
    ProjectTimesheetOverviewItemRead,
    ProjectTimesheetOverviewRead,
    ProjectTimesheetRecordRead,
    ProjectTimesheetUpdateRequest,
    ProjectTimesheetWorkerOptionRead,
    ProjectTimesheetWorkspaceRead,
)
from .team_leader_bonus import calculate_team_leader_bonus, get_month_bounds

TWO_DECIMALS = Decimal("0.01")


def _build_timesheet_team_leader_name_expression():
    return (
        select(func.coalesce(TalentProfile.full_name, User.name))
        .select_from(User)
        .outerjoin(TalentProfile, TalentProfile.user_id == User.id)
        .where(
            User.id == ProjectTimesheetRecord.team_leader_user_id,
            User.is_deleted.is_(False),
        )
        .limit(1)
        .scalar_subquery()
    )


def _build_timesheet_registrar_name_expression():
    return (
        select(AdminUser.name)
        .where(
            AdminUser.id == ProjectTimesheetRecord.created_by_admin_user_id,
            AdminUser.is_deleted.is_(False),
        )
        .limit(1)
        .scalar_subquery()
    )


def _get_timesheet_worker_name(user: User, talent: TalentProfile | None) -> str:
    return str((talent.full_name if talent and talent.full_name else None) or user.name or user.email or "").strip()


def _build_timesheet_note_images_expression():
    note_image_count = func.json_length(ProjectTimesheetRecord.data, "$.note_asset_ids")
    return case((note_image_count > 0, 1), else_=None)


TIMESHEET_ADVANCED_FILTER_FIELD_MAP: dict[str, AdvancedFilterFieldDefinition] = {
    "sub_project_name": AdvancedFilterFieldDefinition(
        name="sub_project_name",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.sub_project_name,
    ),
    "work_date": AdvancedFilterFieldDefinition(
        name="work_date",
        filter_kind="date",
        sql_expression=ProjectTimesheetRecord.work_date,
    ),
    "user_name": AdvancedFilterFieldDefinition(
        name="user_name",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.user_name_snapshot,
    ),
    "user_email": AdvancedFilterFieldDefinition(
        name="user_email",
        filter_kind="email",
        sql_expression=ProjectTimesheetRecord.user_email_snapshot,
    ),
    "team_leader_name": AdvancedFilterFieldDefinition(
        name="team_leader_name",
        filter_kind="select",
        sql_expression=_build_timesheet_team_leader_name_expression(),
    ),
    "project_manager_name": AdvancedFilterFieldDefinition(
        name="project_manager_name",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.project_manager_name_snapshot,
    ),
    "registrar_name": AdvancedFilterFieldDefinition(
        name="registrar_name",
        filter_kind="text",
        sql_expression=_build_timesheet_registrar_name_expression(),
    ),
    "language": AdvancedFilterFieldDefinition(
        name="language",
        filter_kind="select",
        sql_expression=ProjectTimesheetRecord.language,
    ),
    "work_type": AdvancedFilterFieldDefinition(
        name="work_type",
        filter_kind="select",
        sql_expression=ProjectTimesheetRecord.work_type,
    ),
    "output_quantity": AdvancedFilterFieldDefinition(
        name="output_quantity",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.output_quantity,
    ),
    "customer_human_efficiency_minutes": AdvancedFilterFieldDefinition(
        name="customer_human_efficiency_minutes",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.customer_human_efficiency_minutes,
    ),
    "candidate_human_efficiency_minutes": AdvancedFilterFieldDefinition(
        name="candidate_human_efficiency_minutes",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.candidate_human_efficiency_minutes,
    ),
    "customer_duration_hours": AdvancedFilterFieldDefinition(
        name="customer_duration_hours",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.customer_duration_hours,
    ),
    "candidate_duration_hours": AdvancedFilterFieldDefinition(
        name="candidate_duration_hours",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.candidate_duration_hours,
    ),
    "role_name": AdvancedFilterFieldDefinition(
        name="role_name",
        filter_kind="select",
        sql_expression=ProjectTimesheetRecord.role_name,
    ),
    "non_operational_duration_hours": AdvancedFilterFieldDefinition(
        name="non_operational_duration_hours",
        filter_kind="number",
        sql_expression=ProjectTimesheetRecord.non_operational_duration_hours,
    ),
    "project_link": AdvancedFilterFieldDefinition(
        name="project_link",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.project_link,
    ),
    "poc_evaluation": AdvancedFilterFieldDefinition(
        name="poc_evaluation",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.poc_evaluation,
    ),
    "extra_notes": AdvancedFilterFieldDefinition(
        name="extra_notes",
        filter_kind="text",
        sql_expression=ProjectTimesheetRecord.extra_notes,
    ),
    "note_images": AdvancedFilterFieldDefinition(
        name="note_images",
        filter_kind="file",
        sql_expression=_build_timesheet_note_images_expression(),
    ),
}


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def _quantize_hours(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)


def _quantize_customer_duration_hours(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(TWO_DECIMALS, rounding=ROUND_CEILING)


def _quantize_candidate_duration_hours(value: Decimal | None) -> Decimal | None:
    return _quantize_hours(value)


def _zero_decimal() -> Decimal:
    return Decimal("0.00")


def _resolve_contract_rate(contract_record: ContractRecord, job: Job) -> Decimal | None:
    return _quantize_hours(_to_decimal(contract_record.rate)) or _quantize_hours(_to_decimal(job.compensation_min))


def _get_company_timesheet_languages(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_LANGUAGES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _get_company_timesheet_work_types(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _get_company_timesheet_roles(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_ROLES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


async def _get_company_and_project(
    *,
    company_id: int,
    project_id: int,
    db: AsyncSession,
) -> tuple[AdminCompany, AdminCompanyProject]:
    company_result = await db.execute(
        select(AdminCompany).where(
            AdminCompany.id == company_id,
            AdminCompany.is_deleted.is_(False),
        )
    )
    company = company_result.scalar_one_or_none()
    if company is None:
        raise NotFoundException("Company not found.")

    project_result = await db.execute(
        select(AdminCompanyProject).where(
            AdminCompanyProject.id == project_id,
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        raise NotFoundException("Project not found.")

    return company, project


async def list_active_project_workers(
    *,
    company_id: int,
    project_id: int,
    db: AsyncSession,
    contract_type: str | None = None,
) -> list[dict[str, Any]]:
    conditions = [
        ContractRecord.is_deleted.is_(False),
        ContractRecord.is_current.is_(True),
        ContractRecord.contract_status == CONTRACT_STATUS_ACTIVE,
        User.is_deleted.is_(False),
    ]
    if contract_type is not None:
        conditions.append(ContractRecord.contract_type == contract_type)

    result = await db.execute(
        select(ContractRecord, User, TalentProfile)
        .join(User, User.id == ContractRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.id == ContractRecord.talent_profile_id)
        .where(*conditions)
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.id.desc())
    )

    workers: list[dict[str, Any]] = []
    for record, user, talent in result.all():
        workers.append(
            ProjectTimesheetWorkerOptionRead(
                user_id=int(user.id),
                talent_profile_id=int(talent.id) if talent is not None else record.talent_profile_id,
                contract_record_id=int(record.id),
                contract_type=record.contract_type,
                name=_get_timesheet_worker_name(user, talent),
                email=record.user_snapshot_email or user.email,
                agreement_ref_no=record.agreement_ref_no,
            ).model_dump(),
        )

    return sorted(
        workers,
        key=lambda item: (str(item["name"]).casefold(), int(item["contract_record_id"] or 0)),
    )


async def list_active_project_team_leaders(
    *,
    company_id: int,
    project_id: int,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    return await list_active_project_workers(
        company_id=company_id,
        project_id=project_id,
        db=db,
    )


def _serialize_note_assets(
    record: ProjectTimesheetRecord,
    asset_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_ids = (record.data or {}).get("note_asset_ids")
    if not isinstance(raw_ids, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_id in raw_ids:
        try:
            asset_id = int(raw_id)
        except Exception:
            continue
        payload = asset_map.get(asset_id)
        if payload is None:
            continue
        items.append(
            ProjectTimesheetNoteAssetRead(
                asset_id=asset_id,
                name=str(payload["original_name"]),
                preview_url=payload.get("preview_url"),
                download_url=payload.get("download_url"),
                mime_type=payload.get("mime_type"),
            ).model_dump()
        )
    return items


def _serialize_timesheet_record(
    record: ProjectTimesheetRecord,
    *,
    asset_map: dict[int, dict[str, Any]],
    team_leader_map: dict[int, dict[str, Any]] | None = None,
    admin_user_map: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    team_leader = team_leader_map.get(int(record.team_leader_user_id or 0)) if team_leader_map else None
    registrar = admin_user_map.get(int(record.created_by_admin_user_id or 0)) if admin_user_map else None
    return ProjectTimesheetRecordRead(
        id=record.id,
        company_id=record.company_id,
        project_id=record.project_id,
        sub_project_name=record.sub_project_name,
        work_date=record.work_date,
        user_id=record.user_id,
        talent_profile_id=record.talent_profile_id,
        contract_record_id=record.contract_record_id,
        user_name=record.user_name_snapshot or "",
        user_email=record.user_email_snapshot,
        team_leader_user_id=record.team_leader_user_id,
        team_leader_name=team_leader.get("name") if team_leader else None,
        project_manager_admin_user_id=record.project_manager_admin_user_id,
        project_manager_name=record.project_manager_name_snapshot,
        registrar_admin_user_id=record.created_by_admin_user_id,
        registrar_name=registrar.get("name") if registrar else None,
        language=record.language,
        work_type=record.work_type,
        output_quantity=record.output_quantity,
        customer_human_efficiency_minutes=record.customer_human_efficiency_minutes,
        candidate_human_efficiency_minutes=record.candidate_human_efficiency_minutes,
        customer_duration_hours=record.customer_duration_hours,
        candidate_duration_hours=record.candidate_duration_hours,
        role_name=record.role_name,
        non_operational_duration_hours=record.non_operational_duration_hours,
        project_link=record.project_link,
        poc_evaluation=record.poc_evaluation,
        extra_notes=record.extra_notes,
        note_images=_serialize_note_assets(record, asset_map),
        created_at=record.created_at,
        updated_at=record.updated_at,
    ).model_dump()


def _build_timesheet_advanced_filter_record(
    record: ProjectTimesheetRecord,
    *,
    asset_map: dict[int, dict[str, Any]],
    team_leader_map: dict[int, dict[str, Any]] | None = None,
    admin_user_map: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    team_leader = team_leader_map.get(int(record.team_leader_user_id or 0)) if team_leader_map else None
    registrar = admin_user_map.get(int(record.created_by_admin_user_id or 0)) if admin_user_map else None
    note_images = _serialize_note_assets(record, asset_map)
    return {
        "sub_project_name": record.sub_project_name,
        "work_date": record.work_date.isoformat(),
        "user_name": record.user_name_snapshot or "",
        "user_email": record.user_email_snapshot or "",
        "team_leader_name": team_leader.get("name") if team_leader else "",
        "project_manager_name": record.project_manager_name_snapshot or "",
        "registrar_name": registrar.get("name") if registrar else "",
        "language": record.language,
        "work_type": record.work_type,
        "output_quantity": float(record.output_quantity) if record.output_quantity is not None else None,
        "customer_human_efficiency_minutes": (
            float(record.customer_human_efficiency_minutes)
            if record.customer_human_efficiency_minutes is not None
            else None
        ),
        "candidate_human_efficiency_minutes": (
            float(record.candidate_human_efficiency_minutes)
            if record.candidate_human_efficiency_minutes is not None
            else None
        ),
        "customer_duration_hours": (
            float(record.customer_duration_hours) if record.customer_duration_hours is not None else None
        ),
        "candidate_duration_hours": (
            float(record.candidate_duration_hours) if record.candidate_duration_hours is not None else None
        ),
        "role_name": record.role_name or "",
        "non_operational_duration_hours": (
            float(record.non_operational_duration_hours) if record.non_operational_duration_hours is not None else None
        ),
        "project_link": record.project_link or "",
        "poc_evaluation": record.poc_evaluation or "",
        "extra_notes": record.extra_notes or "",
        "note_images": [item.get("name") for item in note_images],
    }


async def _load_team_leader_payload_map(
    *,
    db: AsyncSession,
    user_ids: list[int],
) -> dict[int, dict[str, Any]]:
    normalized_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
    if not normalized_ids:
        return {}

    result = await db.execute(
        select(User, TalentProfile)
        .outerjoin(TalentProfile, TalentProfile.user_id == User.id)
        .where(
            User.id.in_(normalized_ids),
            User.is_deleted.is_(False),
        )
    )
    payload_map: dict[int, dict[str, Any]] = {}
    for user, talent in result.all():
        payload_map[int(user.id)] = {
            "name": (talent.full_name if talent and talent.full_name else None) or user.name,
            "email": (talent.email if talent and talent.email else None) or user.email,
        }
    return payload_map


async def _load_note_asset_payload_map(
    *,
    db: AsyncSession,
    asset_ids: list[int],
) -> dict[int, dict[str, Any]]:
    normalized_ids = sorted({int(asset_id) for asset_id in asset_ids if int(asset_id) > 0})
    if not normalized_ids:
        return {}
    asset_result = await db.execute(
        select(Asset).where(
            Asset.id.in_(normalized_ids),
            Asset.is_deleted.is_(False),
        )
    )
    return {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}


async def _load_admin_user_payload_map(
    *,
    db: AsyncSession,
    admin_user_ids: list[int],
) -> dict[int, dict[str, Any]]:
    normalized_ids = sorted({int(user_id) for user_id in admin_user_ids if int(user_id) > 0})
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(AdminUser).where(
            AdminUser.id.in_(normalized_ids),
            AdminUser.is_deleted.is_(False),
        )
    )
    return {
        int(account.id): {
            "name": account.name,
            "username": account.username,
            "email": account.email,
        }
        for account in result.scalars().all()
    }


async def _validate_timesheet_note_assets(
    *,
    db: AsyncSession,
    note_asset_ids: list[int],
    admin_user_id: int,
) -> None:
    normalized_ids = sorted({int(asset_id) for asset_id in note_asset_ids if int(asset_id) > 0})
    if not normalized_ids:
        return
    assets = await ensure_assets_exist(db, asset_ids=normalized_ids)
    asset_map = {int(asset.id): asset for asset in assets}
    invalid_assets = [
        asset for asset in asset_map.values() if not str(asset.mime_type or "").lower().startswith("image/")
    ]
    if invalid_assets:
        raise BadRequestException("Only image attachments are supported in notes.")
    unauthorized_assets = [
        asset
        for asset in asset_map.values()
        if asset.module != "timesheet"
        or asset.owner_type != "admin_user"
        or int(asset.owner_id or 0) != int(admin_user_id)
    ]
    if unauthorized_assets:
        raise BadRequestException("Invalid timesheet note attachment.")


async def _resolve_project_manager_admin_user(
    *,
    db: AsyncSession,
    admin_user_id: int,
) -> AdminUser:
    result = await db.execute(
        select(AdminUser).where(
            AdminUser.id == admin_user_id,
            AdminUser.is_deleted.is_(False),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise BadRequestException("Selected project manager must be an active admin account.")
    return account


async def _resolve_timesheet_worker(
    *,
    db: AsyncSession,
    company_id: int,
    project_id: int,
    contract_record_id: int,
) -> tuple[ContractRecord, User, TalentProfile | None]:
    result = await db.execute(
        select(ContractRecord, User, TalentProfile)
        .join(User, User.id == ContractRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.id == ContractRecord.talent_profile_id)
        .where(
            ContractRecord.id == contract_record_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.contract_status == CONTRACT_STATUS_ACTIVE,
            User.is_deleted.is_(False),
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise BadRequestException("Selected worker must have an active contract.")
    return row


async def list_project_timesheet_workspace(
    *,
    company_id: int,
    project_id: int,
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
    advanced_filter: str | None = None,
) -> dict[str, Any]:
    advanced_filter_query = parse_advanced_filter_query(advanced_filter)
    if has_advanced_filter_rules(advanced_filter_query):
        validate_advanced_filter_query(advanced_filter_query, field_map=TIMESHEET_ADVANCED_FILTER_FIELD_MAP)
    company, project = await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)

    conditions = [
        ProjectTimesheetRecord.company_id == company_id,
        ProjectTimesheetRecord.project_id == project_id,
        ProjectTimesheetRecord.is_deleted.is_(False),
    ]
    if start_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date >= start_date)
    if end_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date <= end_date)
    normalized_keyword = (keyword or "").strip()
    if normalized_keyword:
        like = f"%{normalized_keyword}%"
        conditions.append(
            or_(
                ProjectTimesheetRecord.sub_project_name.ilike(like),
                ProjectTimesheetRecord.user_name_snapshot.ilike(like),
                ProjectTimesheetRecord.user_email_snapshot.ilike(like),
                ProjectTimesheetRecord.language.ilike(like),
                ProjectTimesheetRecord.work_type.ilike(like),
                ProjectTimesheetRecord.role_name.ilike(like),
                ProjectTimesheetRecord.project_link.ilike(like),
                ProjectTimesheetRecord.extra_notes.ilike(like),
                ProjectTimesheetRecord.poc_evaluation.ilike(like),
            )
        )

    available_team_leader_ids_result = await db.execute(
        select(ProjectTimesheetRecord.team_leader_user_id)
        .where(
            *conditions,
            ProjectTimesheetRecord.team_leader_user_id.is_not(None),
            ProjectTimesheetRecord.team_leader_user_id > 0,
        )
        .distinct()
    )
    available_team_leader_ids = [
        int(user_id)
        for user_id in available_team_leader_ids_result.scalars().all()
        if user_id is not None and int(user_id) > 0
    ]
    available_team_leader_map = await _load_team_leader_payload_map(
        db=db,
        user_ids=available_team_leader_ids,
    )
    available_team_leaders = sorted(
        {
            str((available_team_leader_map.get(int(user_id or 0)) or {}).get("name") or "").strip()
            for user_id in available_team_leader_ids
        }
        - {""},
        key=str.casefold,
    )

    filtered_conditions = list(conditions)
    advanced_filter_condition = build_advanced_filter_query_sql_condition(
        advanced_filter_query,
        field_map=TIMESHEET_ADVANCED_FILTER_FIELD_MAP,
    )
    if advanced_filter_condition is not None:
        filtered_conditions.append(advanced_filter_condition)

    records_result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(*filtered_conditions)
        .order_by(ProjectTimesheetRecord.work_date.desc(), ProjectTimesheetRecord.id.desc())
    )
    filtered_records = records_result.scalars().all()

    note_asset_ids = {
        int(asset_id)
        for record in filtered_records
        for asset_id in ((record.data or {}).get("note_asset_ids") or [])
        if isinstance(asset_id, int) or str(asset_id).isdigit()
    }
    asset_map: dict[int, dict[str, Any]] = {}
    if note_asset_ids:
        assets_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(note_asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in assets_result.scalars().all()}

    team_leader_map = await _load_team_leader_payload_map(
        db=db,
        user_ids=[int(record.team_leader_user_id) for record in filtered_records if record.team_leader_user_id],
    )
    admin_user_map = await _load_admin_user_payload_map(
        db=db,
        admin_user_ids=[
            int(record.created_by_admin_user_id) for record in filtered_records if record.created_by_admin_user_id
        ],
    )

    dashboard_totals_by_language: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {
            "customer_duration_hours": _zero_decimal(),
            "candidate_duration_hours": _zero_decimal(),
        }
    )
    for record in filtered_records:
        language = str(record.language or "").strip()
        if not language:
            continue
        dashboard_totals_by_language[language]["customer_duration_hours"] += (
            _quantize_customer_duration_hours(_to_decimal(record.customer_duration_hours)) or _zero_decimal()
        )
        dashboard_totals_by_language[language]["candidate_duration_hours"] += (
            _quantize_candidate_duration_hours(_to_decimal(record.candidate_duration_hours)) or _zero_decimal()
        )
    dashboard_items = [
        ProjectTimesheetDashboardItemRead(
            language=language,
            customer_duration_hours=_quantize_customer_duration_hours(payload["customer_duration_hours"])
            or _zero_decimal(),
            candidate_duration_hours=_quantize_candidate_duration_hours(payload["candidate_duration_hours"])
            or _zero_decimal(),
            total_duration_hours=(payload["customer_duration_hours"] + payload["candidate_duration_hours"]).quantize(
                TWO_DECIMALS, rounding=ROUND_HALF_UP
            ),
        ).model_dump()
        for language, payload in sorted(dashboard_totals_by_language.items(), key=lambda item: item[0].casefold())
    ]

    latest_created_at = max((record.created_at for record in filtered_records), default=None)
    active_workers = await list_active_project_workers(company_id=company_id, project_id=project_id, db=db)
    active_team_leader_workers = await list_active_project_team_leaders(
        company_id=company_id,
        project_id=project_id,
        db=db,
    )

    return ProjectTimesheetWorkspaceRead(
        company_id=company.id,
        company_name=company.name,
        project_id=project.id,
        project_name=project.name,
        timesheet_languages=_get_company_timesheet_languages(company),
        timesheet_work_types=_get_company_timesheet_work_types(company),
        timesheet_roles=_get_company_timesheet_roles(company),
        available_team_leaders=available_team_leaders,
        available_team_leader_workers=active_team_leader_workers,
        available_workers=active_workers,
        latest_created_at=latest_created_at,
        dashboard_items=dashboard_items,
        records=[
            _serialize_timesheet_record(
                record,
                asset_map=asset_map,
                team_leader_map=team_leader_map,
                admin_user_map=admin_user_map,
            )
            for record in filtered_records
        ],
        start_date=start_date,
        end_date=end_date,
    ).model_dump()


async def list_project_timesheet_overview(
    *,
    db: AsyncSession,
    company_id: int | None = None,
) -> dict[str, Any]:
    conditions = [
        AdminCompany.is_deleted.is_(False),
        AdminCompanyProject.is_deleted.is_(False),
    ]
    if company_id is not None:
        conditions.append(AdminCompany.id == company_id)

    result = await db.execute(
        select(
            AdminCompany.id,
            AdminCompany.name,
            AdminCompanyProject.id,
            AdminCompanyProject.name,
            func.count(ProjectTimesheetRecord.id),
            func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
            func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
            func.max(ProjectTimesheetRecord.created_at),
        )
        .join(AdminCompanyProject, AdminCompanyProject.company_id == AdminCompany.id)
        .outerjoin(
            ProjectTimesheetRecord,
            (ProjectTimesheetRecord.company_id == AdminCompany.id)
            & (ProjectTimesheetRecord.project_id == AdminCompanyProject.id)
            & (ProjectTimesheetRecord.is_deleted.is_(False)),
        )
        .where(*conditions)
        .group_by(AdminCompany.id, AdminCompany.name, AdminCompanyProject.id, AdminCompanyProject.name)
        .order_by(AdminCompany.name.asc(), AdminCompanyProject.name.asc())
    )

    items = [
        ProjectTimesheetOverviewItemRead(
            company_id=int(raw_company_id),
            company_name=str(company_name),
            project_id=int(raw_project_id),
            project_name=str(project_name),
            record_count=int(record_count or 0),
            customer_duration_hours=_quantize_customer_duration_hours(_to_decimal(customer_hours)) or _zero_decimal(),
            candidate_duration_hours=(
                _quantize_candidate_duration_hours(_to_decimal(candidate_hours)) or _zero_decimal()
            ),
            latest_created_at=latest_created_at,
        ).model_dump()
        for (
            raw_company_id,
            company_name,
            raw_project_id,
            project_name,
            record_count,
            customer_hours,
            candidate_hours,
            latest_created_at,
        ) in result.all()
    ]

    return ProjectTimesheetOverviewRead(items=items, company_id=company_id).model_dump()


def _build_timesheet_analytics_conditions(
    *,
    company_id: int | None = None,
    project_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    language: str | None = None,
    work_type: str | None = None,
    role_name: str | None = None,
    keyword: str | None = None,
) -> list[Any]:
    conditions: list[Any] = [
        ProjectTimesheetRecord.is_deleted.is_(False),
        AdminCompany.is_deleted.is_(False),
        AdminCompanyProject.is_deleted.is_(False),
    ]
    if company_id is not None:
        conditions.append(ProjectTimesheetRecord.company_id == company_id)
    if project_id is not None:
        conditions.append(ProjectTimesheetRecord.project_id == project_id)
    if start_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date >= start_date)
    if end_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date <= end_date)
    if language:
        conditions.append(ProjectTimesheetRecord.language == language)
    if work_type:
        conditions.append(ProjectTimesheetRecord.work_type == work_type)
    if role_name:
        conditions.append(ProjectTimesheetRecord.role_name == role_name)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                AdminCompany.name.ilike(like),
                AdminCompanyProject.name.ilike(like),
                ProjectTimesheetRecord.sub_project_name.ilike(like),
                ProjectTimesheetRecord.user_name_snapshot.ilike(like),
                ProjectTimesheetRecord.user_email_snapshot.ilike(like),
                ProjectTimesheetRecord.language.ilike(like),
                ProjectTimesheetRecord.work_type.ilike(like),
                ProjectTimesheetRecord.role_name.ilike(like),
                ProjectTimesheetRecord.project_link.ilike(like),
                ProjectTimesheetRecord.extra_notes.ilike(like),
                ProjectTimesheetRecord.poc_evaluation.ilike(like),
            )
        )
    return conditions


def _timesheet_analytics_select_from(statement: Any) -> Any:
    return (
        statement.select_from(ProjectTimesheetRecord)
        .join(AdminCompany, AdminCompany.id == ProjectTimesheetRecord.company_id)
        .join(
            AdminCompanyProject,
            (AdminCompanyProject.id == ProjectTimesheetRecord.project_id)
            & (AdminCompanyProject.company_id == AdminCompany.id),
        )
    )


def _build_timesheet_metric_item(
    *,
    key: str,
    label: str | None,
    record_count: int | None,
    output_quantity: Any,
    customer_duration_hours: Any,
    candidate_duration_hours: Any,
    non_operational_duration_hours: Any,
    company_id: int | None = None,
    company_name: str | None = None,
    project_id: int | None = None,
    project_name: str | None = None,
    user_id: int | None = None,
    user_email: str | None = None,
) -> ProjectTimesheetAnalyticsMetricItemRead:
    return ProjectTimesheetAnalyticsMetricItemRead(
        key=key,
        label=label or "未填写",
        company_id=company_id,
        company_name=company_name,
        project_id=project_id,
        project_name=project_name,
        user_id=user_id,
        user_email=user_email,
        record_count=int(record_count or 0),
        output_quantity=_quantize_hours(_to_decimal(output_quantity)) or _zero_decimal(),
        customer_duration_hours=(
            _quantize_customer_duration_hours(_to_decimal(customer_duration_hours)) or _zero_decimal()
        ),
        candidate_duration_hours=(
            _quantize_candidate_duration_hours(_to_decimal(candidate_duration_hours)) or _zero_decimal()
        ),
        non_operational_duration_hours=_quantize_hours(_to_decimal(non_operational_duration_hours)) or _zero_decimal(),
    )


async def _load_timesheet_filter_options(
    *,
    db: AsyncSession,
    conditions: list[Any],
) -> ProjectTimesheetAnalyticsFilterOptionRead:
    async def load_distinct(column: Any) -> list[str]:
        result = await db.execute(
            _timesheet_analytics_select_from(select(column))
            .where(*conditions, column.is_not(None), column != "")
            .distinct()
            .order_by(column.asc())
        )
        return [str(item).strip() for item in result.scalars().all() if str(item or "").strip()]

    return ProjectTimesheetAnalyticsFilterOptionRead(
        languages=await load_distinct(ProjectTimesheetRecord.language),
        work_types=await load_distinct(ProjectTimesheetRecord.work_type),
        roles=await load_distinct(ProjectTimesheetRecord.role_name),
    )


async def list_project_timesheet_analytics(
    *,
    db: AsyncSession,
    company_id: int | None = None,
    project_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    language: str | None = None,
    work_type: str | None = None,
    role_name: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise BadRequestException("Start date cannot be later than end date.")

    scope_company_name: str | None = None
    scope_project_name: str | None = None
    if company_id is not None:
        company = await db.get(AdminCompany, company_id)
        if company is None or company.is_deleted:
            raise NotFoundException("Company not found.")
        scope_company_name = company.name
    if project_id is not None:
        project_result = await db.execute(
            select(AdminCompanyProject).where(
                AdminCompanyProject.id == project_id,
                AdminCompanyProject.is_deleted.is_(False),
                *([AdminCompanyProject.company_id == company_id] if company_id is not None else []),
            )
        )
        project = project_result.scalar_one_or_none()
        if project is None:
            raise NotFoundException("Project not found.")
        scope_project_name = project.name
        if company_id is None:
            company_id = int(project.company_id)
            company = await db.get(AdminCompany, company_id)
            scope_company_name = company.name if company is not None and not company.is_deleted else None

    conditions = _build_timesheet_analytics_conditions(
        company_id=company_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        language=(language or "").strip() or None,
        work_type=(work_type or "").strip() or None,
        role_name=(role_name or "").strip() or None,
        keyword=(keyword or "").strip() or None,
    )

    summary_result = await db.execute(
        _timesheet_analytics_select_from(
            select(
                func.count(ProjectTimesheetRecord.id),
                func.count(func.distinct(ProjectTimesheetRecord.company_id)),
                func.count(func.distinct(ProjectTimesheetRecord.project_id)),
                func.count(func.distinct(ProjectTimesheetRecord.user_id)),
                func.count(func.distinct(ProjectTimesheetRecord.sub_project_name)),
                func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
                func.max(func.coalesce(ProjectTimesheetRecord.updated_at, ProjectTimesheetRecord.created_at)),
            )
        ).where(*conditions)
    )
    (
        record_count,
        company_count,
        project_count,
        person_count,
        sub_project_count,
        output_quantity,
        customer_hours,
        candidate_hours,
        non_operational_hours,
        latest_created_at,
    ) = summary_result.one()

    summary = ProjectTimesheetAnalyticsSummaryRead(
        company_count=int(company_count or 0),
        project_count=int(project_count or 0),
        person_count=int(person_count or 0),
        sub_project_count=int(sub_project_count or 0),
        record_count=int(record_count or 0),
        output_quantity=_quantize_hours(_to_decimal(output_quantity)) or _zero_decimal(),
        customer_duration_hours=_quantize_customer_duration_hours(_to_decimal(customer_hours)) or _zero_decimal(),
        candidate_duration_hours=_quantize_candidate_duration_hours(_to_decimal(candidate_hours)) or _zero_decimal(),
        non_operational_duration_hours=_quantize_hours(_to_decimal(non_operational_hours)) or _zero_decimal(),
        latest_created_at=latest_created_at,
    )

    trend_result = await db.execute(
        _timesheet_analytics_select_from(
            select(
                ProjectTimesheetRecord.work_date,
                func.count(ProjectTimesheetRecord.id),
                func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
            )
        )
        .where(*conditions)
        .group_by(ProjectTimesheetRecord.work_date)
        .order_by(ProjectTimesheetRecord.work_date.asc())
    )
    trend = [
        ProjectTimesheetAnalyticsTrendItemRead(
            date=raw_date,
            record_count=int(raw_count or 0),
            output_quantity=_quantize_hours(_to_decimal(raw_output)) or _zero_decimal(),
            customer_duration_hours=_quantize_customer_duration_hours(_to_decimal(raw_customer)) or _zero_decimal(),
            candidate_duration_hours=_quantize_candidate_duration_hours(_to_decimal(raw_candidate)) or _zero_decimal(),
            non_operational_duration_hours=_quantize_hours(_to_decimal(raw_non_operational)) or _zero_decimal(),
        )
        for raw_date, raw_count, raw_output, raw_customer, raw_candidate, raw_non_operational in trend_result.all()
    ]

    async def load_breakdown(
        *group_columns: Any,
        order_by_candidate: bool = True,
        limit: int | None = 12,
    ) -> list[Any]:
        candidate_sum = func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0)
        statement = (
            _timesheet_analytics_select_from(
                select(
                    *group_columns,
                    func.count(ProjectTimesheetRecord.id),
                    func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                    func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                    candidate_sum,
                    func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
                )
            )
            .where(*conditions)
            .group_by(*group_columns)
            .order_by(candidate_sum.desc() if order_by_candidate else func.count(ProjectTimesheetRecord.id).desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await db.execute(statement)
        return result.all()

    company_breakdown = [
        _build_timesheet_metric_item(
            key=f"company-{raw_company_id}",
            label=company_name,
            company_id=int(raw_company_id),
            company_name=str(company_name),
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_company_id,
            company_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(AdminCompany.id, AdminCompany.name, limit=12)
    ]

    project_breakdown = [
        _build_timesheet_metric_item(
            key=f"project-{raw_project_id}",
            label=project_name,
            company_id=int(raw_company_id),
            company_name=str(company_name),
            project_id=int(raw_project_id),
            project_name=str(project_name),
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_company_id,
            company_name,
            raw_project_id,
            project_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            AdminCompany.id, AdminCompany.name, AdminCompanyProject.id, AdminCompanyProject.name, limit=12
        )
    ]

    language_breakdown = [
        _build_timesheet_metric_item(
            key=f"language-{language_value or 'blank'}",
            label=language_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            language_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.language,
            limit=12,
        )
    ]

    work_type_breakdown = [
        _build_timesheet_metric_item(
            key=f"work-type-{work_type_value or 'blank'}",
            label=work_type_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            work_type_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.work_type,
            limit=12,
        )
    ]

    role_breakdown = [
        _build_timesheet_metric_item(
            key=f"role-{role_value or 'blank'}",
            label=role_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            role_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.role_name,
            limit=12,
        )
    ]

    person_ranking = [
        _build_timesheet_metric_item(
            key=f"user-{raw_user_id}",
            label=user_name,
            user_id=int(raw_user_id),
            user_email=user_email,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_user_id,
            user_name,
            user_email,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.user_id,
            ProjectTimesheetRecord.user_name_snapshot,
            ProjectTimesheetRecord.user_email_snapshot,
            limit=10,
        )
    ]

    sub_project_ranking = [
        _build_timesheet_metric_item(
            key=f"sub-project-{sub_project_name or 'blank'}",
            label=sub_project_name,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            sub_project_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.sub_project_name,
            limit=10,
        )
    ]

    filter_options = await _load_timesheet_filter_options(db=db, conditions=conditions)

    return ProjectTimesheetAnalyticsRead(
        company_id=company_id,
        company_name=scope_company_name,
        project_id=project_id,
        project_name=scope_project_name,
        start_date=start_date,
        end_date=end_date,
        summary=summary,
        trend=trend,
        company_breakdown=company_breakdown,
        project_breakdown=project_breakdown,
        language_breakdown=language_breakdown,
        work_type_breakdown=work_type_breakdown,
        role_breakdown=role_breakdown,
        person_ranking=person_ranking,
        sub_project_ranking=sub_project_ranking,
        filter_options=filter_options,
    ).model_dump()


def _is_local_team_leader_record(record: ProjectTimesheetRecord) -> bool:
    marker_text = " ".join(
        part
        for part in [
            record.role_name,
            record.work_type,
        ]
        if part
    ).casefold()
    if not marker_text:
        return False
    compact_text = re.sub(r"[\s_-]+", " ", marker_text).strip()
    tokens = set(re.findall(r"[a-z0-9]+", compact_text))
    return (
        any(
            marker in marker_text
            for marker in [
                "local team leader",
                "team leader",
                "leader",
                "小组长",
                "组长",
            ]
        )
        or "tl" in tokens
    )


def _serialize_candidate_timesheet_entry(
    record: ProjectTimesheetRecord,
    *,
    project_name: str | None,
) -> dict[str, Any]:
    return CandidateTimesheetEntryRead(
        id=record.id,
        contract_record_id=int(record.contract_record_id or 0),
        project_id=record.project_id,
        project_name=None,
        project_code=record.sub_project_name,
        work_date=record.work_date,
        hours=_quantize_hours(_to_decimal(record.candidate_duration_hours)) or _zero_decimal(),
    ).model_dump()


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except Exception:
        return None


def _serialize_candidate_referral_rewards(contract_record: ContractRecord) -> list[dict[str, Any]]:
    raw_rewards = (contract_record.data or {}).get("referral_rewards")
    if not isinstance(raw_rewards, list):
        return []
    rewards: list[dict[str, Any]] = []
    for raw_reward in raw_rewards:
        if not isinstance(raw_reward, dict):
            continue
        referred_candidate = str(raw_reward.get("referred_candidate") or "").strip()
        if not referred_candidate:
            continue
        rewards.append(
            CandidateTimesheetReferralRewardRead(
                referred_candidate=referred_candidate,
                onboarding_date=_parse_date_value(raw_reward.get("onboarding_date")),
                status=(str(raw_reward.get("status")).strip() if raw_reward.get("status") not in (None, "") else None),
                work_hours=_quantize_hours(_to_decimal(raw_reward.get("work_hours"))) or _zero_decimal(),
                referral_earnings=_quantize_hours(_to_decimal(raw_reward.get("referral_earnings"))) or _zero_decimal(),
            ).model_dump()
        )
    return rewards


async def list_candidate_timesheet_workspace(
    *,
    user_id: int,
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
    bonus_month: str | None = None,
) -> dict[str, Any]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise BadRequestException("Start date cannot be later than end date.")

    bonus_start_date, bonus_end_date, normalized_bonus_month = get_month_bounds(bonus_month)

    contract_result = await db.execute(
        select(ContractRecord, Job, AdminCompany, AdminCompanyProject)
        .join(Job, Job.id == ContractRecord.job_id)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
        .where(
            ContractRecord.user_id == user_id,
            ContractRecord.is_deleted.is_(False),
            Job.is_deleted.is_(False),
        )
        .order_by(ContractRecord.is_current.desc(), ContractRecord.updated_at.desc(), ContractRecord.id.desc())
    )
    contract_rows = contract_result.all()
    if not contract_rows:
        return CandidateTimesheetWorkspaceRead(
            contracts=[],
            start_date=start_date,
            end_date=end_date,
            bonus_month=normalized_bonus_month,
        ).model_dump()

    contract_ids = [int(contract_record.id) for contract_record, _, _, _ in contract_rows]
    has_active_team_leader_contract = any(
        contract_record.is_current
        and contract_record.contract_status == CONTRACT_STATUS_ACTIVE
        and contract_record.contract_type == CONTRACT_TYPE_TEAM_LEADER
        for contract_record, _, _, _ in contract_rows
    )
    record_conditions = [
        ProjectTimesheetRecord.user_id == user_id,
        ProjectTimesheetRecord.contract_record_id.in_(contract_ids),
        ProjectTimesheetRecord.is_deleted.is_(False),
    ]
    if start_date is not None:
        record_conditions.append(ProjectTimesheetRecord.work_date >= start_date)
    if end_date is not None:
        record_conditions.append(ProjectTimesheetRecord.work_date <= end_date)

    timesheet_result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(*record_conditions)
        .order_by(ProjectTimesheetRecord.work_date.desc(), ProjectTimesheetRecord.id.desc())
    )
    records = timesheet_result.scalars().all()
    records_by_contract_id: dict[int, list[ProjectTimesheetRecord]] = defaultdict(list)
    for record in records:
        if record.contract_record_id is None:
            continue
        records_by_contract_id[int(record.contract_record_id)].append(record)

    project_name_by_id: dict[int, str] = {}
    for _, _, _, project in contract_rows:
        if project is not None:
            project_name_by_id[int(project.id)] = project.name

    team_leader_hours_result = await db.execute(
        select(func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0)).where(
            ProjectTimesheetRecord.team_leader_user_id == user_id,
            ProjectTimesheetRecord.work_date >= bonus_start_date,
            ProjectTimesheetRecord.work_date <= bonus_end_date,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    monthly_team_hours = _quantize_hours(_to_decimal(team_leader_hours_result.scalar_one_or_none())) or _zero_decimal()
    bonus_multiplier, team_performance_bonus = calculate_team_leader_bonus(monthly_team_hours)
    team_leader_bonus_payload = CandidateTimesheetTeamLeaderBonusRead(
        month=normalized_bonus_month,
        monthly_team_hours=monthly_team_hours,
        bonus_multiplier=bonus_multiplier,
        team_performance_bonus=team_performance_bonus,
    ).model_dump()

    contracts: list[dict[str, Any]] = []
    for contract_record, job, company, project in contract_rows:
        contract_records = records_by_contract_id.get(int(contract_record.id), [])
        work_hour_rows: list[dict[str, Any]] = []
        local_team_leader_rows: list[dict[str, Any]] = []
        latest_updated_at: datetime | None = None
        total_work_hours = _zero_decimal()

        for record in contract_records:
            project_name = project_name_by_id.get(int(record.project_id))
            entry = _serialize_candidate_timesheet_entry(record, project_name=project_name)
            hours = _quantize_hours(_to_decimal(record.candidate_duration_hours)) or _zero_decimal()
            total_work_hours += hours
            record_timestamp = record.updated_at or record.created_at
            if latest_updated_at is None or record_timestamp > latest_updated_at:
                latest_updated_at = record_timestamp
            work_hour_rows.append(entry)

        referral_rewards = _serialize_candidate_referral_rewards(contract_record)
        referral_earnings = sum(
            (
                _quantize_hours(_to_decimal(reward.get("referral_earnings"))) or _zero_decimal()
                for reward in referral_rewards
            ),
            _zero_decimal(),
        ).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
        rate = _resolve_contract_rate(contract_record, job)
        estimated_income = (total_work_hours * (rate or _zero_decimal())).quantize(
            TWO_DECIMALS,
            rounding=ROUND_HALF_UP,
        )

        contracts.append(
            CandidateTimesheetContractRead(
                contract_record_id=contract_record.id,
                previous_contract_record_id=contract_record.previous_contract_record_id,
                is_current=contract_record.is_current,
                contract_type=contract_record.contract_type,
                agreement_ref_no=contract_record.agreement_ref_no,
                contract_status=contract_record.contract_status,
                job_id=job.id,
                job_title=job.title,
                job_country=job.country,
                job_languages=list((job.data or {}).get(JOB_DATA_LANGUAGES_KEY) or []),
                service_customer_company_id=None,
                service_customer_company_name=None,
                service_customer_project_id=None,
                service_customer_project_name=None,
                rate=rate,
                rate_unit=job.compensation_unit,
                effective_date=contract_record.effective_date,
                end_date=contract_record.end_date,
                work_hours=work_hour_rows,
                local_team_leader_hours=local_team_leader_rows,
                team_leader_bonus=None,
                referral_rewards=referral_rewards,
                dashboard=CandidateTimesheetDashboardRead(
                    latest_updated_at=latest_updated_at,
                    total_work_hours=total_work_hours.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP),
                    referral_earnings=referral_earnings,
                    team_leader_bonus=_zero_decimal(),
                    estimated_income=estimated_income,
                ),
            ).model_dump()
        )

    contracts.sort(
        key=lambda item: (
            item["contract_status"] in INACTIVE_CONTRACT_STATUSES or not item["is_current"],
            -int(item["contract_record_id"]),
        )
    )
    return CandidateTimesheetWorkspaceRead(
        contracts=contracts,
        start_date=start_date,
        end_date=end_date,
        bonus_month=normalized_bonus_month,
        team_leader_bonus=team_leader_bonus_payload if has_active_team_leader_contract else None,
    ).model_dump()


async def create_project_timesheet_records(
    *,
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchCreateRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    company, project = await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)

    timesheet_languages = _get_company_timesheet_languages(company)
    if timesheet_languages and payload.language not in timesheet_languages:
        raise BadRequestException("Selected language is not configured for this company.")

    timesheet_work_types = set(_get_company_timesheet_work_types(company))
    timesheet_roles = set(_get_company_timesheet_roles(company))
    worker_options = await list_active_project_workers(company_id=company_id, project_id=project_id, db=db)
    worker_map = {
        int(worker["contract_record_id"]): worker for worker in worker_options if worker.get("contract_record_id")
    }
    team_leader_options = await list_active_project_team_leaders(company_id=company_id, project_id=project_id, db=db)
    active_team_leader_user_ids = {int(worker["user_id"]) for worker in team_leader_options}
    if int(payload.team_leader_user_id) > 0 and int(payload.team_leader_user_id) not in active_team_leader_user_ids:
        raise BadRequestException("Selected team leader must have an active contract.")
    project_manager = await _resolve_project_manager_admin_user(
        db=db,
        admin_user_id=int(payload.project_manager_admin_user_id),
    )

    note_asset_ids = sorted(
        {int(asset_id) for entry in payload.entries for asset_id in entry.note_asset_ids if int(asset_id) > 0}
    )
    await _validate_timesheet_note_assets(
        db=db,
        note_asset_ids=note_asset_ids,
        admin_user_id=admin_user_id,
    )

    created_count = 0
    for entry in payload.entries:
        worker = worker_map.get(int(entry.contract_record_id))
        if worker is None:
            raise BadRequestException("Selected worker must have an active contract.")
        if entry.user_id is not None and int(entry.user_id) != int(worker["user_id"]):
            raise BadRequestException("Selected worker does not match the selected contract.")
        if timesheet_work_types and entry.work_type not in timesheet_work_types:
            raise BadRequestException("Selected work type is not configured for this company.")
        role_name = (entry.role_name or "").strip() or None
        if timesheet_roles and role_name and role_name not in timesheet_roles:
            raise BadRequestException("Selected role is not configured for this company.")

        record = ProjectTimesheetRecord(
            company_id=company.id,
            project_id=project.id,
            sub_project_name=payload.sub_project_name,
            work_date=entry.work_date,
            user_id=int(worker["user_id"]),
            talent_profile_id=worker.get("talent_profile_id"),
            contract_record_id=int(worker["contract_record_id"]),
            user_name_snapshot=str(worker["name"]),
            user_email_snapshot=worker.get("email"),
            project_manager_admin_user_id=int(project_manager.id),
            project_manager_name_snapshot=project_manager.name,
            language=payload.language,
            work_type=entry.work_type,
            output_quantity=_quantize_hours(entry.output_quantity),
            customer_human_efficiency_minutes=_quantize_hours(payload.customer_human_efficiency_minutes),
            candidate_human_efficiency_minutes=_quantize_hours(payload.candidate_human_efficiency_minutes),
            customer_duration_hours=_quantize_customer_duration_hours(entry.customer_duration_hours),
            candidate_duration_hours=_quantize_candidate_duration_hours(entry.candidate_duration_hours),
            role_name=role_name,
            non_operational_duration_hours=_quantize_hours(entry.non_operational_duration_hours),
            project_link=payload.project_link,
            poc_evaluation=(entry.poc_evaluation or "").strip() or None,
            extra_notes=(entry.extra_notes or "").strip() or None,
            created_by_admin_user_id=admin_user_id,
            updated_by_admin_user_id=admin_user_id,
            data={
                "note_asset_ids": list(entry.note_asset_ids),
            },
        )
        record.team_leader_user_id = int(payload.team_leader_user_id) or None
        db.add(record)
        created_count += 1

    await db.flush()
    return ProjectTimesheetBatchCreateResponse(created_count=created_count).model_dump()


async def update_project_timesheet_record(
    *,
    company_id: int,
    project_id: int,
    record_id: int,
    payload: ProjectTimesheetUpdateRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    company, project = await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)
    result = await db.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.id == record_id,
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundException("Timesheet record not found.")

    timesheet_languages = _get_company_timesheet_languages(company)
    if timesheet_languages and payload.language not in timesheet_languages:
        raise BadRequestException("Selected language is not configured for this company.")

    timesheet_work_types = set(_get_company_timesheet_work_types(company))
    if timesheet_work_types and payload.work_type not in timesheet_work_types:
        raise BadRequestException("Selected work type is not configured for this company.")

    role_name = (payload.role_name or "").strip() or None
    timesheet_roles = set(_get_company_timesheet_roles(company))
    if timesheet_roles and role_name and role_name not in timesheet_roles:
        raise BadRequestException("Selected role is not configured for this company.")

    team_leader_options = await list_active_project_team_leaders(company_id=company_id, project_id=project_id, db=db)
    active_team_leader_user_ids = {int(worker["user_id"]) for worker in team_leader_options}
    if int(payload.team_leader_user_id) > 0 and int(payload.team_leader_user_id) not in active_team_leader_user_ids:
        raise BadRequestException("Selected team leader must have an active contract.")

    await _validate_timesheet_note_assets(
        db=db,
        note_asset_ids=payload.note_asset_ids,
        admin_user_id=admin_user_id,
    )

    contract_record, user, talent = await _resolve_timesheet_worker(
        db=db,
        company_id=company.id,
        project_id=project.id,
        contract_record_id=int(payload.contract_record_id),
    )
    if (
        int(payload.contract_record_id) != int(record.contract_record_id or 0)
        and contract_record.contract_status != "Active"
    ):
        raise BadRequestException("Selected worker must have an active contract.")
    if payload.user_id is not None and int(payload.user_id) != int(user.id):
        raise BadRequestException("Selected worker does not match the selected contract.")

    record.sub_project_name = payload.sub_project_name
    record.work_date = payload.work_date
    record.user_id = int(user.id)
    record.talent_profile_id = int(talent.id) if talent is not None else contract_record.talent_profile_id
    record.contract_record_id = int(contract_record.id)
    record.user_name_snapshot = _get_timesheet_worker_name(user, talent)
    record.user_email_snapshot = contract_record.user_snapshot_email or user.email
    record.team_leader_user_id = int(payload.team_leader_user_id) or None
    record.language = payload.language
    record.work_type = payload.work_type
    record.output_quantity = _quantize_hours(payload.output_quantity)
    record.customer_human_efficiency_minutes = _quantize_hours(payload.customer_human_efficiency_minutes)
    record.candidate_human_efficiency_minutes = _quantize_hours(payload.candidate_human_efficiency_minutes)
    record.customer_duration_hours = _quantize_customer_duration_hours(payload.customer_duration_hours)
    record.candidate_duration_hours = _quantize_candidate_duration_hours(payload.candidate_duration_hours)
    record.role_name = role_name
    record.non_operational_duration_hours = _quantize_hours(payload.non_operational_duration_hours)
    record.project_link = payload.project_link
    record.poc_evaluation = (payload.poc_evaluation or "").strip() or None
    record.extra_notes = (payload.extra_notes or "").strip() or None
    record.updated_by_admin_user_id = admin_user_id
    record.updated_at = datetime.now(UTC)
    record.data = {
        **(record.data or {}),
        "note_asset_ids": list(payload.note_asset_ids),
    }

    await db.flush()
    await db.refresh(record)
    asset_map = await _load_note_asset_payload_map(
        db=db,
        asset_ids=[int(asset_id) for asset_id in payload.note_asset_ids],
    )
    team_leader_map = await _load_team_leader_payload_map(
        db=db,
        user_ids=[int(record.team_leader_user_id)] if record.team_leader_user_id else [],
    )
    admin_user_map = await _load_admin_user_payload_map(
        db=db,
        admin_user_ids=[int(record.created_by_admin_user_id)] if record.created_by_admin_user_id else [],
    )
    return _serialize_timesheet_record(
        record,
        asset_map=asset_map,
        team_leader_map=team_leader_map,
        admin_user_map=admin_user_map,
    )


async def delete_project_timesheet_records(
    *,
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchDeleteRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)
    result = await db.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.id.in_(payload.record_ids),
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    records = result.scalars().all()
    now = datetime.now(UTC)
    for record in records:
        record.is_deleted = True
        record.deleted_at = now
        record.updated_at = now
        record.updated_by_admin_user_id = admin_user_id

    await db.flush()
    return ProjectTimesheetBatchDeleteResponse(deleted_count=len(records)).model_dump()
