from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions.http_exceptions import BadRequestException
from ..modules.admin.company.model import AdminCompany, AdminCompanyProject
from ..modules.contract_record.const import CONTRACT_STATUS_ACTIVE, CONTRACT_TYPE_TEAM_LEADER
from ..modules.contract_record.model import ContractRecord
from ..modules.payable.calculator import (
    calculate_referral_milestones,
    calculate_salary,
    calculate_team_leader_pay,
)
from ..modules.payable.commands import upsert_pending_payable
from ..modules.payable.const import PayableStatus
from ..modules.payable.model import Payable, PayableTimesheetSource
from ..modules.payable.schema import PayableDraft
from ..modules.payable.source_keys import (
    referral_reward_source_key,
    salary_source_key,
    team_leader_bonus_source_key,
)
from ..modules.project_timesheet_record.model import ProjectTimesheetRecord
from ..modules.referral.model import ReferralRecord
from ..modules.referral_bonus_model.service import REFERRAL_BONUS_MILESTONES_DATA_KEY
from ..modules.talent_profile.model import TalentProfile
from ..modules.user.model import User

_ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class SettlementSyncResult:
    settlement_month: str
    created_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    frozen_count: int = 0


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    record_id: int
    source_version: int
    work_hours: Decimal
    amount_contribution: Decimal


@dataclass(frozen=True, slots=True)
class _MaterializedPayable:
    draft: PayableDraft
    sources: tuple[_SourceSnapshot, ...]


@dataclass(slots=True)
class _SalaryGroup:
    contract: ContractRecord
    user: User
    talent: TalentProfile | None
    company: AdminCompany | None
    project: AdminCompanyProject | None
    records: list[ProjectTimesheetRecord]


_TeamLeaderContext = tuple[
    ContractRecord,
    User,
    TalentProfile | None,
]


def _month_bounds(settlement_month: str) -> tuple[date, date]:
    try:
        year_text, month_text = settlement_month.split("-", 1)
        year = int(year_text)
        month = int(month_text)
        start = date(year, month, 1)
    except (TypeError, ValueError) as exc:
        raise BadRequestException("Invalid settlement month.") from exc
    if settlement_month != f"{year:04d}-{month:02d}":
        raise BadRequestException("Invalid settlement month.")
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _hours(record: ProjectTimesheetRecord) -> Decimal:
    return Decimal(record.candidate_duration_hours or _ZERO).quantize(Decimal("0.01"))


def _display_name(*, user: User, talent: TalentProfile | None, contract: ContractRecord) -> str:
    return str(
        (talent.full_name if talent is not None else None)
        or contract.contractor_name
        or contract.user_snapshot_name
        or user.name
        or user.email
    )


def _languages(records: Sequence[ProjectTimesheetRecord]) -> list[str]:
    return list(dict.fromkeys(record.language for record in records if record.language))


async def _build_salary_payables(
    *,
    db: AsyncSession,
    settlement_month: str,
    start: date,
    end: date,
) -> list[_MaterializedPayable]:
    result = await db.execute(
        select(ProjectTimesheetRecord, ContractRecord, User, TalentProfile, AdminCompany, AdminCompanyProject)
        .join(ContractRecord, ContractRecord.id == ProjectTimesheetRecord.contract_record_id)
        .join(User, User.id == ProjectTimesheetRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.id == ProjectTimesheetRecord.talent_profile_id)
        .outerjoin(AdminCompany, AdminCompany.id == ProjectTimesheetRecord.company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ProjectTimesheetRecord.project_id)
        .where(
            ProjectTimesheetRecord.work_date >= start,
            ProjectTimesheetRecord.work_date < end,
            ProjectTimesheetRecord.is_deleted.is_(False),
            ContractRecord.is_deleted.is_(False),
            User.is_deleted.is_(False),
        )
        .order_by(ProjectTimesheetRecord.id.asc())
    )
    groups: dict[tuple[int, int], _SalaryGroup] = {}
    for record, contract, user, talent, company, project in result.all():
        if _hours(record) <= 0 or Decimal(contract.rate or _ZERO) <= 0:
            continue
        key = (int(user.id), int(contract.id))
        group = groups.get(key)
        if group is None:
            group = _SalaryGroup(
                contract=contract,
                user=user,
                talent=talent,
                company=company,
                project=project,
                records=[],
            )
            groups[key] = group
        group.records.append(record)

    materialized: list[_MaterializedPayable] = []
    for group in groups.values():
        rate = Decimal(group.contract.rate or _ZERO)
        sources = tuple(
            _SourceSnapshot(
                record_id=int(record.id),
                source_version=int(record.version),
                work_hours=_hours(record),
                amount_contribution=calculate_salary(work_hours=_hours(record), rate=rate),
            )
            for record in group.records
        )
        total_hours = sum((source.work_hours for source in sources), start=_ZERO)
        amount = calculate_salary(work_hours=total_hours, rate=rate)
        materialized.append(
            _MaterializedPayable(
                draft=PayableDraft(
                    source_key=salary_source_key(
                        month=settlement_month,
                        user_id=int(group.user.id),
                        contract_record_id=int(group.contract.id),
                    ),
                    payment_type="salary",
                    settlement_month=settlement_month,
                    user_id=int(group.user.id),
                    talent_profile_id=group.contract.talent_profile_id,
                    contract_record_id=int(group.contract.id),
                    company_id=group.contract.service_customer_company_id,
                    project_id=group.contract.service_customer_project_id,
                    amount=amount,
                    calculation_snapshot={
                        "work_hours": str(total_hours),
                        "rate": str(rate),
                        "source_record_count": len(sources),
                        "languages": _languages(group.records),
                    },
                    user_snapshot_name=_display_name(
                        user=group.user,
                        talent=group.talent,
                        contract=group.contract,
                    ),
                    user_snapshot_email=group.contract.user_snapshot_email or group.user.email,
                    company_snapshot_name=group.company.name if group.company is not None else None,
                    project_snapshot_name=group.project.name if group.project is not None else None,
                    contract_snapshot_ref_no=group.contract.agreement_ref_no,
                ),
                sources=sources,
            )
        )
    return materialized


async def _load_team_leader_contexts(
    *,
    db: AsyncSession,
    leader_user_ids: set[int],
) -> dict[int, _TeamLeaderContext]:
    if not leader_user_ids:
        return {}
    result = await db.execute(
        select(ContractRecord, User, TalentProfile)
        .join(User, User.id == ContractRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.user_id == User.id)
        .where(
            ContractRecord.user_id.in_(leader_user_ids),
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.contract_status == CONTRACT_STATUS_ACTIVE,
            ContractRecord.contract_type == CONTRACT_TYPE_TEAM_LEADER,
            User.is_deleted.is_(False),
        )
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.id.desc())
    )
    contexts: dict[int, _TeamLeaderContext] = {}
    for contract, user, talent in result.all():
        contexts.setdefault(int(user.id), (contract, user, talent))
    return contexts


async def _build_team_leader_payables(
    *,
    db: AsyncSession,
    settlement_month: str,
    start: date,
    end: date,
) -> list[_MaterializedPayable]:
    records = list(
        (
            await db.scalars(
                select(ProjectTimesheetRecord)
                .where(
                    ProjectTimesheetRecord.work_date >= start,
                    ProjectTimesheetRecord.work_date < end,
                    ProjectTimesheetRecord.team_leader_user_id.is_not(None),
                    ProjectTimesheetRecord.team_leader_user_id > 0,
                    ProjectTimesheetRecord.is_deleted.is_(False),
                )
                .order_by(ProjectTimesheetRecord.id.asc())
            )
        ).all()
    )
    leader_user_ids = {
        int(record.team_leader_user_id)
        for record in records
        if record.team_leader_user_id is not None and _hours(record) > 0
    }
    contexts = await _load_team_leader_contexts(db=db, leader_user_ids=leader_user_ids)
    grouped_records: dict[tuple[int, int], list[ProjectTimesheetRecord]] = defaultdict(list)
    for record in records:
        if record.team_leader_user_id is None or _hours(record) <= 0:
            continue
        key = (int(record.team_leader_user_id), int(record.project_id))
        if key[0] in contexts:
            grouped_records[key].append(record)

    company_ids = {int(group[0].company_id) for group in grouped_records.values()}
    project_ids = {int(group[0].project_id) for group in grouped_records.values()}
    company_map = {
        int(company.id): company
        for company in (await db.scalars(select(AdminCompany).where(AdminCompany.id.in_(company_ids)))).all()
    }
    project_map = {
        int(project.id): project
        for project in (
            await db.scalars(select(AdminCompanyProject).where(AdminCompanyProject.id.in_(project_ids)))
        ).all()
    }

    materialized: list[_MaterializedPayable] = []
    for dimension, group_records in grouped_records.items():
        contract, user, talent = contexts[dimension[0]]
        source_company_id = int(group_records[0].company_id)
        source_project_id = int(group_records[0].project_id)
        company = company_map.get(source_company_id)
        project = project_map.get(source_project_id)
        total_hours = sum((_hours(record) for record in group_records), start=_ZERO)
        calculation = calculate_team_leader_pay(base_pay=contract.base_pay, monthly_team_hours=total_hours)
        source_snapshots: list[_SourceSnapshot] = []
        for index, record in enumerate(group_records):
            contribution = (_hours(record) * calculation.multiplier).quantize(Decimal("0.01"))
            if index == 0:
                contribution += calculation.base_pay
            source_snapshots.append(
                _SourceSnapshot(
                    record_id=int(record.id),
                    source_version=int(record.version),
                    work_hours=_hours(record),
                    amount_contribution=contribution,
                )
            )
        materialized.append(
            _MaterializedPayable(
                draft=PayableDraft(
                    source_key=team_leader_bonus_source_key(
                        month=settlement_month,
                        user_id=int(user.id),
                        project_id=dimension[1],
                    ),
                    payment_type="team_leader_bonus",
                    settlement_month=settlement_month,
                    user_id=int(user.id),
                    talent_profile_id=contract.talent_profile_id,
                    contract_record_id=int(contract.id),
                    company_id=source_company_id,
                    project_id=source_project_id,
                    amount=calculation.amount,
                    calculation_snapshot={
                        "work_hours": str(total_hours),
                        "base_pay": str(calculation.base_pay),
                        "bonus": str(calculation.bonus),
                        "multiplier": str(calculation.multiplier),
                        "source_record_count": len(source_snapshots),
                        "languages": _languages(group_records),
                    },
                    user_snapshot_name=_display_name(user=user, talent=talent, contract=contract),
                    user_snapshot_email=contract.user_snapshot_email or user.email,
                    company_snapshot_name=company.name if company is not None else None,
                    project_snapshot_name=project.name if project is not None else None,
                    contract_snapshot_ref_no=contract.agreement_ref_no,
                ),
                sources=tuple(source_snapshots),
            )
        )
    return materialized


async def _replace_sources(
    *,
    db: AsyncSession,
    payable_id: int,
    sources: Sequence[_SourceSnapshot],
) -> None:
    await db.execute(delete(PayableTimesheetSource).where(PayableTimesheetSource.payable_id == payable_id))
    db.add_all(
        [
            PayableTimesheetSource(
                payable_id=payable_id,
                project_timesheet_record_id=source.record_id,
                source_version=source.source_version,
                work_hours_snapshot=source.work_hours,
                amount_contribution_snapshot=source.amount_contribution,
            )
            for source in sources
        ]
    )
    await db.flush()


async def _build_referral_payables(
    *,
    db: AsyncSession,
    settlement_month: str,
) -> list[_MaterializedPayable]:
    referrals = list(
        (
            await db.scalars(
                select(ReferralRecord)
                .where(ReferralRecord.is_deleted.is_(False))
                .order_by(ReferralRecord.id.asc())
            )
        ).all()
    )
    if not referrals:
        return []

    referrer_ids = {int(record.referrer_user_id) for record in referrals}
    referred_ids = {int(record.referred_user_id) for record in referrals}
    users = list(
        (
            await db.scalars(
                select(User).where(
                    User.id.in_(referrer_ids | referred_ids),
                    User.is_deleted.is_(False),
                )
            )
        ).all()
    )
    user_map = {int(user.id): user for user in users}
    talent_map = {
        int(talent.user_id): talent
        for talent in (
            await db.scalars(
                select(TalentProfile).where(
                    TalentProfile.user_id.in_(referrer_ids),
                    TalentProfile.is_deleted.is_(False),
                )
            )
        ).all()
    }
    timesheets = list(
        (
            await db.scalars(
                select(ProjectTimesheetRecord)
                .where(
                    ProjectTimesheetRecord.user_id.in_(referred_ids),
                    ProjectTimesheetRecord.is_deleted.is_(False),
                )
                .order_by(ProjectTimesheetRecord.work_date.asc(), ProjectTimesheetRecord.id.asc())
            )
        ).all()
    )
    timesheets_by_user: dict[int, list[ProjectTimesheetRecord]] = defaultdict(list)
    for timesheet in timesheets:
        if _hours(timesheet) > 0:
            timesheets_by_user[int(timesheet.user_id)].append(timesheet)
    company_ids = {int(timesheet.company_id) for timesheet in timesheets}
    project_ids = {int(timesheet.project_id) for timesheet in timesheets}
    company_map = {
        int(company.id): company
        for company in (await db.scalars(select(AdminCompany).where(AdminCompany.id.in_(company_ids)))).all()
    }
    project_map = {
        int(project.id): project
        for project in (
            await db.scalars(select(AdminCompanyProject).where(AdminCompanyProject.id.in_(project_ids)))
        ).all()
    }

    materialized: list[_MaterializedPayable] = []
    for referral in referrals:
        referrer = user_map.get(int(referral.referrer_user_id))
        referred = user_map.get(int(referral.referred_user_id))
        if referrer is None or referred is None:
            continue
        source_rows = timesheets_by_user.get(int(referral.referred_user_id), [])
        total_hours = sum((_hours(row) for row in source_rows), start=_ZERO)
        milestones = calculate_referral_milestones(
            work_hours=total_hours,
            reward_cap=referral.reward_cap,
            milestones=list((referral.data or {}).get(REFERRAL_BONUS_MILESTONES_DATA_KEY) or []),
        )
        for milestone in milestones:
            cumulative_hours = _ZERO
            crossing_index: int | None = None
            for index, row in enumerate(source_rows):
                cumulative_hours += _hours(row)
                if cumulative_hours >= milestone.required_hours:
                    crossing_index = index
                    break
            if crossing_index is None:
                continue
            crossing_row = source_rows[crossing_index]
            crossing_month = crossing_row.work_date.strftime("%Y-%m")
            if crossing_month != settlement_month:
                continue
            milestone_sources = source_rows[: crossing_index + 1]
            snapshots = tuple(
                _SourceSnapshot(
                    record_id=int(row.id),
                    source_version=int(row.version),
                    work_hours=_hours(row),
                    amount_contribution=(
                        milestone.reward_amount if index == crossing_index else _ZERO
                    ),
                )
                for index, row in enumerate(milestone_sources)
            )
            company = company_map.get(int(crossing_row.company_id))
            project = project_map.get(int(crossing_row.project_id))
            referrer_talent = talent_map.get(int(referrer.id))
            materialized.append(
                _MaterializedPayable(
                    draft=PayableDraft(
                        source_key=referral_reward_source_key(
                            referral_record_id=int(referral.id),
                            milestone_index=milestone.milestone_index,
                        ),
                        payment_type="referral_reward",
                        settlement_month=crossing_month,
                        user_id=int(referrer.id),
                        talent_profile_id=referrer_talent.id if referrer_talent is not None else None,
                        referral_record_id=int(referral.id),
                        company_id=int(crossing_row.company_id),
                        project_id=int(crossing_row.project_id),
                        amount=milestone.reward_amount,
                        currency=referral.currency,
                        calculation_snapshot={
                            "milestone_index": milestone.milestone_index,
                            "required_hours": str(milestone.required_hours),
                            "cumulative_hours": str(cumulative_hours),
                            "reward_cap": str(referral.reward_cap),
                            "source_record_count": len(snapshots),
                        },
                        user_snapshot_name=referrer.name or referral.referrer_snapshot_name,
                        user_snapshot_email=referrer.email or referral.referrer_snapshot_email,
                        company_snapshot_name=company.name if company is not None else None,
                        project_snapshot_name=project.name if project is not None else None,
                        referral_referred_user_id=int(referred.id),
                        referral_referred_snapshot_name=referred.name or referral.referred_snapshot_name,
                        referral_referred_snapshot_email=referred.email or referral.referred_snapshot_email,
                    ),
                    sources=snapshots,
                )
            )
    return materialized


async def sync_settlement_month(*, db: AsyncSession, settlement_month: str) -> SettlementSyncResult:
    start, end = _month_bounds(settlement_month)
    await db.scalars(
        select(ProjectTimesheetRecord.id)
        .where(
            ProjectTimesheetRecord.work_date >= start,
            ProjectTimesheetRecord.work_date < end,
        )
        .order_by(ProjectTimesheetRecord.id.asc())
        .with_for_update()
    )
    materialized = [
        *(await _build_salary_payables(db=db, settlement_month=settlement_month, start=start, end=end)),
        *(await _build_team_leader_payables(db=db, settlement_month=settlement_month, start=start, end=end)),
        *(await _build_referral_payables(db=db, settlement_month=settlement_month)),
    ]
    generated_by_key = {item.draft.source_key: item for item in materialized}
    existing = list(
        (
            await db.scalars(
                select(Payable)
                .where(
                    Payable.settlement_month == settlement_month,
                    Payable.payment_type.in_(("salary", "team_leader_bonus", "referral_reward")),
                )
                .with_for_update()
            )
        ).all()
    )
    existing_by_key = {payable.source_key: payable for payable in existing}
    created_count = 0
    updated_count = 0
    deleted_count = 0
    frozen_count = 0

    for source_key, item in generated_by_key.items():
        current = existing_by_key.get(source_key)
        if current is not None and current.status != PayableStatus.PENDING.value:
            frozen_count += 1
            continue
        payable = await upsert_pending_payable(db=db, draft=item.draft)
        await _replace_sources(db=db, payable_id=int(payable.id), sources=item.sources)
        if current is None:
            created_count += 1
        else:
            updated_count += 1

    for payable in existing:
        if payable.source_key in generated_by_key or payable.status != PayableStatus.PENDING.value:
            continue
        await db.execute(delete(PayableTimesheetSource).where(PayableTimesheetSource.payable_id == payable.id))
        await db.delete(payable)
        deleted_count += 1
    await db.flush()
    return SettlementSyncResult(
        settlement_month=settlement_month,
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
        frozen_count=frozen_count,
    )


async def sync_timesheet_change(
    *,
    db: AsyncSession,
    settlement_month: str,
    affected_user_ids: Sequence[int] = (),
) -> SettlementSyncResult:
    normalized_user_ids = sorted({int(user_id) for user_id in affected_user_ids})
    affected_months = {settlement_month}
    if normalized_user_ids:
        referral_ids = list(
            (
                await db.scalars(
                    select(ReferralRecord.id).where(
                        ReferralRecord.referred_user_id.in_(normalized_user_ids),
                        ReferralRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        if referral_ids:
            existing_months = (
                await db.scalars(
                    select(Payable.settlement_month).where(
                        Payable.referral_record_id.in_(referral_ids),
                        Payable.payment_type == "referral_reward",
                    )
                )
            ).all()
            affected_months.update(str(month) for month in existing_months)
            work_dates = (
                await db.scalars(
                    select(ProjectTimesheetRecord.work_date).where(
                        ProjectTimesheetRecord.user_id.in_(normalized_user_ids),
                        ProjectTimesheetRecord.is_deleted.is_(False),
                    )
                )
            ).all()
            affected_months.update(work_date.strftime("%Y-%m") for work_date in work_dates)

    requested_result: SettlementSyncResult | None = None
    for month in sorted(affected_months):
        result = await sync_settlement_month(db=db, settlement_month=month)
        if month == settlement_month:
            requested_result = result
    if requested_result is None:
        raise RuntimeError("Requested settlement month was not synchronized.")
    return requested_result


async def sync_contract_rate_change(*, db: AsyncSession, contract_record_id: int) -> list[SettlementSyncResult]:
    contract = await db.get(ContractRecord, contract_record_id)
    if contract is None:
        return []
    months = list(
        (
            await db.scalars(
                select(ProjectTimesheetRecord.work_date)
                .where(
                    or_(
                        ProjectTimesheetRecord.contract_record_id == contract_record_id,
                        (
                            (ProjectTimesheetRecord.team_leader_user_id == contract.user_id)
                            & (ProjectTimesheetRecord.project_id == contract.service_customer_project_id)
                        ),
                    ),
                    ProjectTimesheetRecord.is_deleted.is_(False),
                )
                .distinct()
            )
        ).all()
    )
    month_keys = sorted({f"{work_date.year:04d}-{work_date.month:02d}" for work_date in months})
    return [await sync_settlement_month(db=db, settlement_month=month) for month in month_keys]
