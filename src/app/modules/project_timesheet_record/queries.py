from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
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
    CandidateTimesheetTeamLeaderBonusRead,
    CandidateTimesheetWorkspaceRead,
    ProjectTimesheetDashboardItemRead,
    ProjectTimesheetOverviewItemRead,
    ProjectTimesheetOverviewRead,
    ProjectTimesheetWorkerOptionRead,
    ProjectTimesheetWorkspaceRead,
)
from .serialization import (
    TWO_DECIMALS,
    _get_timesheet_worker_name,
    _quantize_candidate_duration_hours,
    _quantize_customer_duration_hours,
    _quantize_hours,
    _resolve_contract_rate,
    _serialize_candidate_referral_rewards,
    _serialize_candidate_timesheet_entry,
    _serialize_timesheet_record,
    _to_decimal,
    _zero_decimal,
)
from .team_leader_bonus import calculate_team_leader_bonus, get_month_bounds


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
    return row[0], row[1], row[2]


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
        )
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
        available_team_leader_workers=[
            ProjectTimesheetWorkerOptionRead.model_validate(item) for item in active_team_leader_workers
        ],
        available_workers=[ProjectTimesheetWorkerOptionRead.model_validate(item) for item in active_workers],
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
    conditions: list[Any] = [
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
        )
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
    )

    contracts: list[CandidateTimesheetContractRead] = []
    for contract_record, job, company, project in contract_rows:
        contract_records = records_by_contract_id.get(int(contract_record.id), [])
        work_hour_rows: list[CandidateTimesheetEntryRead] = []
        local_team_leader_rows: list[CandidateTimesheetEntryRead] = []
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
                _quantize_hours(_to_decimal(reward.referral_earnings)) or _zero_decimal()
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
            )
        )

    contracts.sort(
        key=lambda item: (
            item.contract_status in INACTIVE_CONTRACT_STATUSES or not item.is_current,
            -int(item.contract_record_id),
        )
    )
    return CandidateTimesheetWorkspaceRead(
        contracts=contracts,
        start_date=start_date,
        end_date=end_date,
        bonus_month=normalized_bonus_month,
        team_leader_bonus=team_leader_bonus_payload if has_active_team_leader_contract else None,
    ).model_dump()
