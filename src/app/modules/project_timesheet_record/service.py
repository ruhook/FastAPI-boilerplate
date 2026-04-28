import re
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.company.service import (
    COMPANY_DATA_TIMESHEET_LANGUAGES_KEY,
    COMPANY_DATA_TIMESHEET_ROLES_KEY,
    COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY,
)
from ..assets.model import Asset
from ..assets.service import ensure_assets_exist, serialize_asset
from ..contract_record.model import ContractRecord
from ..contract_record.const import (
    CONTRACT_TYPE_TEAM_LEADER,
    INACTIVE_CONTRACT_STATUSES,
)
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
    ProjectTimesheetBatchCreateRequest,
    ProjectTimesheetBatchCreateResponse,
    ProjectTimesheetBatchDeleteRequest,
    ProjectTimesheetBatchDeleteResponse,
    ProjectTimesheetDashboardItemRead,
    ProjectTimesheetNoteAssetRead,
    ProjectTimesheetRecordRead,
    ProjectTimesheetUpdateRequest,
    ProjectTimesheetWorkerOptionRead,
    ProjectTimesheetWorkspaceRead,
)
from .team_leader_bonus import calculate_team_leader_bonus, get_month_bounds

TWO_DECIMALS = Decimal("0.01")


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


def _zero_decimal() -> Decimal:
    return Decimal("0.00")


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
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(ContractRecord, User, TalentProfile)
        .join(User, User.id == ContractRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.id == ContractRecord.talent_profile_id)
        .where(
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.service_customer_company_id == company_id,
            ContractRecord.service_customer_project_id == project_id,
            ContractRecord.contract_status == "Active",
            User.is_deleted.is_(False),
        )
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.id.desc())
    )

    workers: list[dict[str, Any]] = []
    for record, user, talent in result.all():
        workers.append(
            ProjectTimesheetWorkerOptionRead(
                user_id=int(user.id),
                talent_profile_id=int(talent.id) if talent is not None else record.talent_profile_id,
                contract_record_id=int(record.id),
                name=(
                    (talent.full_name if talent and talent.full_name else None)
                    or record.contractor_name
                    or record.user_snapshot_name
                    or user.name
                ),
                email=record.user_snapshot_email or user.email,
                agreement_ref_no=record.agreement_ref_no,
            ).model_dump(),
        )

    return sorted(
        workers,
        key=lambda item: (str(item["name"]).casefold(), int(item["contract_record_id"] or 0)),
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
) -> dict[str, Any]:
    team_leader = team_leader_map.get(int(record.team_leader_user_id or 0)) if team_leader_map else None
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
        language=record.language,
        work_type=record.work_type,
        output_quantity=record.output_quantity,
        human_efficiency_minutes=record.human_efficiency_minutes,
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
            ContractRecord.service_customer_company_id == company_id,
            ContractRecord.service_customer_project_id == project_id,
            User.is_deleted.is_(False),
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise BadRequestException("Selected worker is not available for this project.")
    return row


async def list_project_timesheet_workspace(
    *,
    company_id: int,
    project_id: int,
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
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

    records_result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(*conditions)
        .order_by(ProjectTimesheetRecord.work_date.desc(), ProjectTimesheetRecord.id.desc())
    )
    records = records_result.scalars().all()

    note_asset_ids = {
        int(asset_id)
        for record in records
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

    dashboard_result = await db.execute(
        select(
            ProjectTimesheetRecord.language,
            func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
            func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
        )
        .where(*conditions)
        .group_by(ProjectTimesheetRecord.language)
        .order_by(ProjectTimesheetRecord.language.asc())
    )
    dashboard_items = [
        ProjectTimesheetDashboardItemRead(
            language=str(language),
            customer_duration_hours=_quantize_hours(_to_decimal(customer_hours)) or Decimal("0.00"),
            candidate_duration_hours=_quantize_hours(_to_decimal(candidate_hours)) or Decimal("0.00"),
            total_duration_hours=(
                (_quantize_hours(_to_decimal(customer_hours)) or Decimal("0.00"))
                + (_quantize_hours(_to_decimal(candidate_hours)) or Decimal("0.00"))
            ).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP),
        ).model_dump()
        for language, customer_hours, candidate_hours in dashboard_result.all()
    ]

    latest_result = await db.execute(
        select(func.max(ProjectTimesheetRecord.created_at)).where(*conditions)
    )
    latest_created_at = latest_result.scalar_one_or_none()
    team_leader_map = await _load_team_leader_payload_map(
        db=db,
        user_ids=[int(record.team_leader_user_id) for record in records if record.team_leader_user_id],
    )

    return ProjectTimesheetWorkspaceRead(
        company_id=company.id,
        company_name=company.name,
        project_id=project.id,
        project_name=project.name,
        timesheet_languages=_get_company_timesheet_languages(company),
        timesheet_work_types=_get_company_timesheet_work_types(company),
        timesheet_roles=_get_company_timesheet_roles(company),
        available_workers=await list_active_project_workers(company_id=company_id, project_id=project_id, db=db),
        latest_created_at=latest_created_at,
        dashboard_items=dashboard_items,
        records=[
            _serialize_timesheet_record(record, asset_map=asset_map, team_leader_map=team_leader_map)
            for record in records
        ],
        start_date=start_date,
        end_date=end_date,
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
    return any(
        marker in marker_text
        for marker in [
            "local team leader",
            "team leader",
            "leader",
            "小组长",
            "组长",
        ]
    ) or "tl" in tokens


def _serialize_candidate_timesheet_entry(
    record: ProjectTimesheetRecord,
    *,
    project_name: str | None,
) -> dict[str, Any]:
    return CandidateTimesheetEntryRead(
        id=record.id,
        contract_record_id=int(record.contract_record_id or 0),
        project_id=record.project_id,
        project_name=project_name,
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
        is_team_leader_contract = contract_record.contract_type == CONTRACT_TYPE_TEAM_LEADER
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
                _quantize_hours(_to_decimal(reward.get("referral_earnings")))
                or _zero_decimal()
                for reward in referral_rewards
            ),
            _zero_decimal(),
        ).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
        rate = _quantize_hours(_to_decimal(contract_record.rate))
        team_leader_bonus = team_performance_bonus if is_team_leader_contract else _zero_decimal()
        estimated_income = (total_work_hours * (rate or _zero_decimal()) + referral_earnings + team_leader_bonus).quantize(
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
                service_customer_company_id=contract_record.service_customer_company_id,
                service_customer_company_name=company.name if company is not None else None,
                service_customer_project_id=contract_record.service_customer_project_id,
                service_customer_project_name=project.name if project is not None else None,
                rate=rate,
                rate_unit=job.compensation_unit,
                effective_date=contract_record.effective_date,
                end_date=contract_record.end_date,
                work_hours=work_hour_rows,
                local_team_leader_hours=local_team_leader_rows,
                team_leader_bonus=(
                    team_leader_bonus_payload if is_team_leader_contract else None
                ),
                referral_rewards=referral_rewards,
                dashboard=CandidateTimesheetDashboardRead(
                    latest_updated_at=latest_updated_at,
                    total_work_hours=total_work_hours.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP),
                    referral_earnings=referral_earnings,
                    team_leader_bonus=team_leader_bonus,
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
    worker_map = {int(worker["contract_record_id"]): worker for worker in worker_options if worker.get("contract_record_id")}
    active_worker_user_ids = {int(worker["user_id"]) for worker in worker_options}
    if int(payload.team_leader_user_id) not in active_worker_user_ids:
        raise BadRequestException("Selected team leader is not available for this project.")

    note_asset_ids = sorted(
        {
            int(asset_id)
            for entry in payload.entries
            for asset_id in entry.note_asset_ids
            if int(asset_id) > 0
        }
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
            raise BadRequestException("Selected worker is not available for this project.")
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
            work_date=payload.work_date,
            user_id=int(worker["user_id"]),
            talent_profile_id=worker.get("talent_profile_id"),
            contract_record_id=int(worker["contract_record_id"]),
            user_name_snapshot=str(worker["name"]),
            user_email_snapshot=worker.get("email"),
            language=payload.language,
            work_type=entry.work_type,
            output_quantity=_quantize_hours(entry.output_quantity),
            human_efficiency_minutes=_quantize_hours(payload.human_efficiency_minutes),
            customer_duration_hours=_quantize_hours(entry.customer_duration_hours),
            candidate_duration_hours=_quantize_hours(entry.candidate_duration_hours),
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
        record.team_leader_user_id = int(payload.team_leader_user_id)
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

    worker_options = await list_active_project_workers(company_id=company_id, project_id=project_id, db=db)
    active_worker_user_ids = {int(worker["user_id"]) for worker in worker_options}
    if int(payload.team_leader_user_id) not in active_worker_user_ids:
        raise BadRequestException("Selected team leader is not available for this project.")

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
        raise BadRequestException("Selected worker is not available for this project.")
    if payload.user_id is not None and int(payload.user_id) != int(user.id):
        raise BadRequestException("Selected worker does not match the selected contract.")

    record.sub_project_name = payload.sub_project_name
    record.work_date = payload.work_date
    record.user_id = int(user.id)
    record.talent_profile_id = int(talent.id) if talent is not None else contract_record.talent_profile_id
    record.contract_record_id = int(contract_record.id)
    record.user_name_snapshot = (
        (talent.full_name if talent and talent.full_name else None)
        or contract_record.contractor_name
        or contract_record.user_snapshot_name
        or user.name
    )
    record.user_email_snapshot = contract_record.user_snapshot_email or user.email
    record.team_leader_user_id = int(payload.team_leader_user_id)
    record.language = payload.language
    record.work_type = payload.work_type
    record.output_quantity = _quantize_hours(payload.output_quantity)
    record.human_efficiency_minutes = _quantize_hours(payload.human_efficiency_minutes)
    record.customer_duration_hours = _quantize_hours(payload.customer_duration_hours)
    record.candidate_duration_hours = _quantize_hours(payload.candidate_duration_hours)
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
    return _serialize_timesheet_record(record, asset_map=asset_map, team_leader_map=team_leader_map)


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
