import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..contract_record.const import CONTRACT_STATUS_ACTIVE
from ..contract_record.model import ContractRecord
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..payment_record.service import create_referral_reward_payment_record
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .const import (
    REFERRAL_REWARD_CAP,
    REFERRAL_REWARD_MILESTONES,
    REFERRAL_STATUS_PAID,
    REFERRAL_STATUS_READY_TO_PAY,
    REFERRAL_STATUS_TRACKING,
    calculate_referral_reward,
    quantize_decimal,
)
from .model import ReferralRecord
from .schema import AdminReferralGroupRead, AdminReferralSummaryRead, ReferralMilestoneRead, ReferralRecordRead


def _referral_code_expr():
    return func.json_unquote(func.json_extract(User.data, "$.referral_code"))


def _build_referral_code(user_id: int) -> str:
    return f"RF{user_id}{secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:10]}".upper()


def _get_user_display_name(user: User | None, fallback_name: str | None = None) -> str:
    if user is None:
        return fallback_name or "-"
    return user.name or fallback_name or user.email or "-"


async def ensure_user_referral_code(*, user_id: int, db: AsyncSession) -> str:
    user = await db.get(User, user_id)
    if user is None or user.is_deleted:
        raise NotFoundException("User not found.")
    existing = str((user.data or {}).get("referral_code") or "").strip()
    if existing:
        return existing

    for _ in range(5):
        next_code = _build_referral_code(user_id)
        duplicated = await db.execute(select(User.id).where(_referral_code_expr() == next_code).limit(1))
        if duplicated.first() is None:
            user.data = {**(user.data or {}), "referral_code": next_code}
            await db.flush()
            return next_code

    raise BadRequestException("Unable to generate referral code.")


async def create_referral_from_code(
    *,
    db: AsyncSession,
    referral_code: str | None,
    referred_user_id: int,
) -> ReferralRecord | None:
    code = (referral_code or "").strip()
    if not code:
        return None

    referrer_result = await db.execute(
        select(User).where(
            _referral_code_expr() == code,
            User.is_deleted.is_(False),
        )
    )
    referrer = referrer_result.scalar_one_or_none()
    if referrer is None or int(referrer.id) == int(referred_user_id):
        return None

    existing_result = await db.execute(
        select(ReferralRecord).where(
            ReferralRecord.referred_user_id == referred_user_id,
            ReferralRecord.is_deleted.is_(False),
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        return None

    referred_user = await db.get(User, referred_user_id)
    if referred_user is None or referred_user.is_deleted:
        return None

    talent_result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == referred_user_id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = talent_result.scalar_one_or_none()
    record = ReferralRecord(
        referrer_user_id=referrer.id,
        referred_user_id=referred_user.id,
        referred_talent_profile_id=talent.id if talent is not None else None,
        referrer_snapshot_name=referrer.name,
        referrer_snapshot_email=referrer.email,
        referred_snapshot_name=referred_user.name,
        referred_snapshot_email=referred_user.email,
        source_referral_code=code,
        payout_status=REFERRAL_STATUS_TRACKING,
        data={},
    )
    db.add(record)
    await db.flush()
    await create_operation_log(
        db=db,
        user_id=referred_user.id,
        talent_profile_id=talent.id if talent is not None else None,
        log_type=OperationLogType.REFERRAL_CREATED.value,
        data={
            "referral_record_id": record.id,
            "referrer_user_id": referrer.id,
            "referrer_email": referrer.email,
            "source_referral_code": code,
        },
    )
    return record


def _build_milestones(work_hours: Decimal | None = None) -> list[ReferralMilestoneRead]:
    hours = quantize_decimal(work_hours)
    cumulative_reward = Decimal("0.00")
    milestones: list[ReferralMilestoneRead] = []
    for required_hours, reward_amount in REFERRAL_REWARD_MILESTONES:
        cumulative_reward += reward_amount
        milestones.append(
            ReferralMilestoneRead(
                required_hours=required_hours,
                reward_amount=quantize_decimal(cumulative_reward),
                reached=hours >= required_hours,
            )
        )
    return milestones


async def _load_metrics_for_referred_users(
    *,
    db: AsyncSession,
    referred_user_ids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    if not referred_user_ids:
        return {}

    metrics: dict[int, dict[str, Any]] = {
        int(user_id): {
            "work_hours": Decimal("0.00"),
            "first_work_date": None,
            "active_contract_count": 0,
            "any_contract_count": 0,
            "onboarding_date": None,
        }
        for user_id in referred_user_ids
    }

    work_result = await db.execute(
        select(
            ProjectTimesheetRecord.user_id,
            func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
            func.min(ProjectTimesheetRecord.work_date),
        )
        .where(
            ProjectTimesheetRecord.user_id.in_(referred_user_ids),
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
        .group_by(ProjectTimesheetRecord.user_id)
    )
    for user_id, work_hours, first_work_date in work_result.all():
        item = metrics.setdefault(int(user_id), {})
        item["work_hours"] = quantize_decimal(work_hours)
        item["first_work_date"] = first_work_date

    contract_result = await db.execute(
        select(
            ContractRecord.user_id,
            func.sum(case((ContractRecord.contract_status == CONTRACT_STATUS_ACTIVE, 1), else_=0)),
            func.count(ContractRecord.id),
            func.min(ContractRecord.effective_date),
        )
        .where(
            ContractRecord.user_id.in_(referred_user_ids),
            ContractRecord.is_deleted.is_(False),
        )
        .group_by(ContractRecord.user_id)
    )
    for user_id, active_count, any_count, onboarding_date in contract_result.all():
        item = metrics.setdefault(int(user_id), {})
        item["active_contract_count"] = int(active_count or 0)
        item["any_contract_count"] = int(any_count or 0)
        item["onboarding_date"] = onboarding_date

    return metrics


def _serialize_referral_record(
    *,
    record: ReferralRecord,
    referrer: User | None,
    referred: User | None,
    metrics: dict[str, Any],
) -> ReferralRecordRead:
    work_hours = quantize_decimal(metrics.get("work_hours"))
    referral_earnings = calculate_referral_reward(work_hours)
    paid_reward_amount = quantize_decimal(record.paid_reward_amount)
    payable_reward_amount = max(referral_earnings - paid_reward_amount, Decimal("0.00"))
    active_contract_count = int(metrics.get("active_contract_count") or 0)
    any_contract_count = int(metrics.get("any_contract_count") or 0)
    if payable_reward_amount > 0:
        payout_status = REFERRAL_STATUS_READY_TO_PAY
    elif referral_earnings > 0:
        payout_status = REFERRAL_STATUS_PAID
    else:
        payout_status = REFERRAL_STATUS_TRACKING

    return ReferralRecordRead(
        id=record.id,
        referrer_user_id=record.referrer_user_id,
        referrer_name=_get_user_display_name(referrer, record.referrer_snapshot_name),
        referrer_email=referrer.email if referrer is not None else record.referrer_snapshot_email,
        referred_user_id=record.referred_user_id,
        referred_candidate=_get_user_display_name(referred, record.referred_snapshot_name),
        referred_email=referred.email if referred is not None else record.referred_snapshot_email,
        onboarding_date=metrics.get("onboarding_date") or metrics.get("first_work_date"),
        status="Active" if active_contract_count > 0 else ("Inactive" if any_contract_count > 0 else "Working"),
        work_hours=work_hours,
        referral_earnings=referral_earnings,
        paid_reward_amount=paid_reward_amount,
        payable_reward_amount=payable_reward_amount,
        payout_status=payout_status,
        last_paid_at=record.last_paid_at,
    )


async def _list_referral_records(
    *,
    db: AsyncSession,
    referrer_user_id: int | None = None,
    keyword: str | None = None,
) -> list[ReferralRecordRead]:
    referrer_alias = aliased(User)
    referred_alias = aliased(User)
    conditions = [ReferralRecord.is_deleted.is_(False)]
    if referrer_user_id is not None:
        conditions.append(ReferralRecord.referrer_user_id == referrer_user_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                ReferralRecord.referrer_snapshot_name.like(like),
                ReferralRecord.referrer_snapshot_email.like(like),
                ReferralRecord.referred_snapshot_name.like(like),
                ReferralRecord.referred_snapshot_email.like(like),
                referrer_alias.name.like(like),
                referrer_alias.email.like(like),
                referred_alias.name.like(like),
                referred_alias.email.like(like),
            )
        )

    rows_result = await db.execute(
        select(ReferralRecord, referrer_alias, referred_alias)
        .outerjoin(referrer_alias, referrer_alias.id == ReferralRecord.referrer_user_id)
        .outerjoin(referred_alias, referred_alias.id == ReferralRecord.referred_user_id)
        .where(*conditions)
        .order_by(ReferralRecord.updated_at.desc(), ReferralRecord.id.desc())
    )
    rows = rows_result.all()
    metrics_by_user_id = await _load_metrics_for_referred_users(
        db=db,
        referred_user_ids=[int(record.referred_user_id) for record, _, _ in rows],
    )
    items: list[ReferralRecordRead] = []
    for record, referrer, referred in rows:
        metrics = metrics_by_user_id.get(int(record.referred_user_id), {})
        has_active_contract = int(metrics.get("active_contract_count") or 0) > 0
        has_work_hours = quantize_decimal(metrics.get("work_hours")) > 0
        if not has_active_contract and not has_work_hours:
            continue
        items.append(
            _serialize_referral_record(
                record=record,
                referrer=referrer,
                referred=referred,
                metrics=metrics,
            )
        )
    return items


async def _build_referral_read_for_record(
    *,
    db: AsyncSession,
    record: ReferralRecord,
    require_visible: bool = False,
) -> ReferralRecordRead:
    referrer = await db.get(User, int(record.referrer_user_id))
    referred = await db.get(User, int(record.referred_user_id))
    metrics_by_user_id = await _load_metrics_for_referred_users(
        db=db,
        referred_user_ids=[int(record.referred_user_id)],
    )
    metrics = metrics_by_user_id.get(int(record.referred_user_id), {})
    has_active_contract = int(metrics.get("active_contract_count") or 0) > 0
    has_work_hours = quantize_decimal(metrics.get("work_hours")) > 0
    if require_visible and not has_active_contract and not has_work_hours:
        raise BadRequestException("Referral is not eligible for payout yet.")
    return _serialize_referral_record(
        record=record,
        referrer=referrer if referrer is not None and not referrer.is_deleted else None,
        referred=referred if referred is not None and not referred.is_deleted else None,
        metrics=metrics,
    )


async def get_candidate_referral_dashboard(*, user_id: int, db: AsyncSession) -> dict[str, Any]:
    referral_code = await ensure_user_referral_code(user_id=user_id, db=db)
    items = await _list_referral_records(db=db, referrer_user_id=user_id)
    total_rewards = sum((item.referral_earnings for item in items), Decimal("0.00"))
    return {
        "referral_code": referral_code,
        "reward_cap": REFERRAL_REWARD_CAP,
        "total_rewards": quantize_decimal(total_rewards),
        "active_referral_count": len(items),
        "milestones": _build_milestones(),
        "items": items,
    }


async def list_referrals_for_admin(
    *,
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    payout_status: str | None = None,
) -> dict[str, Any]:
    items = await _list_referral_records(db=db, keyword=keyword)
    if payout_status:
        items = [item for item in items if item.payout_status == payout_status]
    group_map: dict[int, dict[str, Any]] = {}
    for item in items:
        referrer_user_id = int(item.referrer_user_id)
        group = group_map.setdefault(
            referrer_user_id,
            {
                "id": referrer_user_id,
                "referrer_user_id": referrer_user_id,
                "referrer_name": item.referrer_name,
                "referrer_email": item.referrer_email,
                "active_referral_count": 0,
                "total_rewards": Decimal("0.00"),
                "paid_rewards": Decimal("0.00"),
                "payable_rewards": Decimal("0.00"),
                "last_paid_at": None,
                "children": [],
            },
        )
        group["children"].append(item)
        group["active_referral_count"] += 1
        group["total_rewards"] += item.referral_earnings
        group["paid_rewards"] += item.paid_reward_amount
        group["payable_rewards"] += item.payable_reward_amount
        if item.last_paid_at and (
            group["last_paid_at"] is None or item.last_paid_at > group["last_paid_at"]
        ):
            group["last_paid_at"] = item.last_paid_at

    groups = [
        AdminReferralGroupRead(
            **{
                **group,
                "total_rewards": quantize_decimal(group["total_rewards"]),
                "paid_rewards": quantize_decimal(group["paid_rewards"]),
                "payable_rewards": quantize_decimal(group["payable_rewards"]),
            }
        )
        for group in group_map.values()
    ]
    groups.sort(
        key=lambda group: (
            -float(group.payable_rewards),
            -(group.last_paid_at.timestamp() if group.last_paid_at else 0),
            group.referrer_name or "",
            group.referrer_user_id,
        )
    )
    total = len(groups)
    summary = AdminReferralSummaryRead(
        active_referral_count=sum(int(group.active_referral_count or 0) for group in groups),
        referrer_count=total,
        total_rewards=quantize_decimal(sum((group.total_rewards for group in groups), Decimal("0.00"))),
        paid_rewards=quantize_decimal(sum((group.paid_rewards for group in groups), Decimal("0.00"))),
        payable_rewards=quantize_decimal(sum((group.payable_rewards for group in groups), Decimal("0.00"))),
    )
    offset = (page - 1) * page_size
    return {
        "items": groups[offset : offset + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "reward_cap": REFERRAL_REWARD_CAP,
        "milestones": _build_milestones(),
    }


async def mark_referral_reward_paid(
    *,
    referral_record_id: int,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    record_result = await db.execute(
        select(ReferralRecord)
        .where(
            ReferralRecord.id == referral_record_id,
            ReferralRecord.is_deleted.is_(False),
        )
        .with_for_update()
    )
    record = record_result.scalar_one_or_none()
    if record is None or record.is_deleted:
        raise NotFoundException("Referral record not found.")

    current_item = await _build_referral_read_for_record(
        db=db,
        record=record,
        require_visible=True,
    )
    if current_item.payable_reward_amount <= 0:
        raise BadRequestException("There is no unpaid referral reward to mark as paid.")

    now = datetime.now(UTC)
    payment_record = await create_referral_reward_payment_record(
        db=db,
        referral_record=record,
        amount=current_item.payable_reward_amount,
        admin_user_id=admin_user_id,
        paid_at=now,
    )
    record.paid_reward_amount = current_item.referral_earnings
    record.payout_status = REFERRAL_STATUS_PAID
    record.last_paid_at = now
    record.last_paid_by_admin_user_id = admin_user_id
    record.data = {
        **(record.data or {}),
        "last_payment_record_id": payment_record.id,
        "last_payment_record_created_at": now.isoformat(),
        "last_paid_reward_amount": str(current_item.referral_earnings),
        "last_paid_increment_amount": str(current_item.payable_reward_amount),
    }
    await db.flush()

    await create_operation_log(
        db=db,
        user_id=record.referrer_user_id,
        log_type=OperationLogType.REFERRAL_REWARD_MARKED_PAID.value,
        data={
            "referral_record_id": record.id,
            "referrer_user_id": record.referrer_user_id,
            "referred_user_id": record.referred_user_id,
            "paid_reward_amount": str(current_item.referral_earnings),
            "paid_increment_amount": str(current_item.payable_reward_amount),
            "payment_record_id": payment_record.id,
            "operator_admin_user_id": admin_user_id,
            "note": "Referral reward payout marked as completed and payment record created.",
        },
    )

    item = await _build_referral_read_for_record(db=db, record=record)
    return item.model_dump()
