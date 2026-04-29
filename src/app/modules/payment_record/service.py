from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..contract_record.model import ContractRecord
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..referral.const import calculate_referral_reward, quantize_decimal
from ..referral.model import ReferralRecord
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .const import PAYMENT_TYPE_REFERRAL_REWARD, normalize_payment_type, quantize_money
from .model import PaymentRecord
from .schema import (
    CandidateEarningsListPage,
    CandidateEarningsRecordRead,
    CandidateEarningsSummaryRead,
    PaymentRecordBatchCreateRequest,
    PaymentRecordContractOptionRead,
    PaymentRecordListPage,
    PaymentRecordOptionsRead,
    PaymentRecordRead,
    PaymentRecordReferralOptionRead,
    PaymentRecordUserOptionRead,
)


def _user_display_name(user: User | None, fallback: str | None = None) -> str:
    if user is None:
        return fallback or "-"
    return user.name or fallback or user.email or "-"


def _serialize_payment_record(record: PaymentRecord) -> PaymentRecordRead:
    return PaymentRecordRead(
        id=record.id,
        user_id=record.user_id,
        talent_profile_id=record.talent_profile_id,
        contract_record_id=record.contract_record_id,
        referral_record_id=record.referral_record_id,
        payment_type=record.payment_type,
        amount=quantize_money(record.amount),
        currency=record.currency,
        paid_at=record.paid_at,
        external_platform=record.external_platform,
        external_transaction_no=record.external_transaction_no,
        remark=record.remark,
        user_name=record.user_snapshot_name,
        user_email=record.user_snapshot_email,
        company_id=record.company_id,
        project_id=record.project_id,
        company_name=record.company_snapshot_name,
        project_name=record.project_snapshot_name,
        contract_ref_no=record.contract_snapshot_ref_no,
        referral_referred_user_id=record.referral_referred_user_id,
        referral_referred_name=record.referral_referred_snapshot_name,
        referral_referred_email=record.referral_referred_snapshot_email,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _serialize_candidate_earning_record(record: PaymentRecord) -> CandidateEarningsRecordRead:
    return CandidateEarningsRecordRead(
        id=record.id,
        contract_record_id=record.contract_record_id,
        referral_record_id=record.referral_record_id,
        payment_type=record.payment_type,
        amount=quantize_money(record.amount),
        currency=record.currency,
        paid_at=record.paid_at,
        external_platform=record.external_platform,
        external_transaction_no=record.external_transaction_no,
        company_id=record.company_id,
        project_id=record.project_id,
        company_name=record.company_snapshot_name,
        project_name=record.project_snapshot_name,
        contract_ref_no=record.contract_snapshot_ref_no,
        referral_referred_user_id=record.referral_referred_user_id,
        referral_referred_name=record.referral_referred_snapshot_name,
        referral_referred_email=record.referral_referred_snapshot_email,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _safe_normalize_payment_type(value: str | None) -> str:
    try:
        return normalize_payment_type(value)
    except ValueError as exc:
        raise BadRequestException("Invalid payment type.") from exc


def _parse_month_range(month: str | None) -> tuple[str, datetime, datetime]:
    now = datetime.now(UTC)
    if not month:
        year = now.year
        month_number = now.month
    else:
        try:
            raw_year, raw_month = month.split("-", 1)
            year = int(raw_year)
            month_number = int(raw_month)
        except Exception as exc:
            raise BadRequestException("Invalid month format.") from exc
        if month_number < 1 or month_number > 12:
            raise BadRequestException("Invalid month format.")

    start = datetime(year, month_number, 1, tzinfo=UTC)
    if month_number == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month_number + 1, 1, tzinfo=UTC)
    return f"{year:04d}-{month_number:02d}", start, end


async def _get_user_with_talent(*, db: AsyncSession, user_id: int) -> tuple[User, TalentProfile | None]:
    user = await db.get(User, user_id)
    if user is None or user.is_deleted:
        raise NotFoundException("User not found.")

    talent_result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == user_id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    return user, talent_result.scalar_one_or_none()


async def _get_contract_context(
    *,
    db: AsyncSession,
    contract_record_id: int | None,
    user_id: int,
) -> tuple[ContractRecord | None, AdminCompany | None, AdminCompanyProject | None]:
    if contract_record_id is None:
        return None, None, None

    result = await db.execute(
        select(ContractRecord, AdminCompany, AdminCompanyProject)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
        .where(
            ContractRecord.id == contract_record_id,
            ContractRecord.is_deleted.is_(False),
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Contract record not found.")
    contract, company, project = row
    if int(contract.user_id) != int(user_id):
        raise BadRequestException("Contract record does not belong to the selected user.")
    return contract, company, project


async def _get_referral_context(
    *,
    db: AsyncSession,
    referral_record_id: int | None,
    user_id: int,
) -> ReferralRecord | None:
    if referral_record_id is None:
        return None

    record = await db.get(ReferralRecord, referral_record_id)
    if record is None or record.is_deleted:
        raise NotFoundException("Referral record not found.")
    if int(record.referrer_user_id) != int(user_id):
        raise BadRequestException("Referral record does not belong to the selected user.")
    return record


async def _create_payment_record(
    *,
    db: AsyncSession,
    admin_user_id: int,
    user_id: int,
    payment_type: str,
    amount: Decimal,
    paid_at: datetime | None = None,
    currency: str = "USD",
    contract_record_id: int | None = None,
    referral_record_id: int | None = None,
    external_platform: str | None = None,
    external_transaction_no: str | None = None,
    remark: str | None = None,
) -> PaymentRecord:
    normalized_payment_type = _safe_normalize_payment_type(payment_type)
    user, talent = await _get_user_with_talent(db=db, user_id=user_id)
    contract, company, project = await _get_contract_context(
        db=db,
        contract_record_id=contract_record_id,
        user_id=user_id,
    )
    referral = await _get_referral_context(db=db, referral_record_id=referral_record_id, user_id=user_id)

    if normalized_payment_type == PAYMENT_TYPE_REFERRAL_REWARD and referral is None:
        raise BadRequestException("Referral reward payment requires a referral record.")
    if normalized_payment_type != PAYMENT_TYPE_REFERRAL_REWARD and referral is not None:
        raise BadRequestException("Only referral reward payments can link a referral record.")

    record = PaymentRecord(
        user_id=user.id,
        talent_profile_id=talent.id if talent is not None else None,
        contract_record_id=contract.id if contract is not None else None,
        referral_record_id=referral.id if referral is not None else None,
        payment_type=normalized_payment_type,
        amount=quantize_money(amount),
        currency=(currency or "USD").strip().upper(),
        paid_at=paid_at or datetime.now(UTC),
        external_platform=external_platform,
        external_transaction_no=external_transaction_no,
        remark=remark,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        company_id=contract.service_customer_company_id if contract is not None else None,
        project_id=contract.service_customer_project_id if contract is not None else None,
        company_snapshot_name=company.name if company is not None else None,
        project_snapshot_name=project.name if project is not None else None,
        contract_snapshot_ref_no=contract.agreement_ref_no if contract is not None else None,
        referral_referred_user_id=referral.referred_user_id if referral is not None else None,
        referral_referred_snapshot_name=referral.referred_snapshot_name if referral is not None else None,
        referral_referred_snapshot_email=referral.referred_snapshot_email if referral is not None else None,
        created_by_admin_user_id=admin_user_id,
        updated_by_admin_user_id=admin_user_id,
        data={},
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def create_payment_records_for_admin(
    *,
    db: AsyncSession,
    admin_user_id: int,
    payload: PaymentRecordBatchCreateRequest,
) -> dict[str, Any]:
    created: list[PaymentRecord] = []
    for item in payload.items:
        if item.payment_type == PAYMENT_TYPE_REFERRAL_REWARD:
            raise BadRequestException("Referral reward payments must be paid from the referral rewards page.")
        created.append(
            await _create_payment_record(
                db=db,
                admin_user_id=admin_user_id,
                user_id=item.user_id,
                payment_type=item.payment_type,
                amount=item.amount,
                paid_at=item.paid_at,
                currency=item.currency,
                contract_record_id=item.contract_record_id,
                referral_record_id=item.referral_record_id,
                external_platform=item.external_platform,
                external_transaction_no=item.external_transaction_no,
                remark=item.remark,
            )
        )

    await create_operation_log(
        db=db,
        user_id=None,
        log_type=OperationLogType.PAYMENT_RECORD_BATCH_CREATED.value,
        data={
            "created_count": len(created),
            "payment_record_ids": [record.id for record in created],
            "operator_admin_user_id": admin_user_id,
        },
    )
    return {
        "items": [_serialize_payment_record(record).model_dump() for record in created],
        "created_count": len(created),
    }


async def create_referral_reward_payment_record(
    *,
    db: AsyncSession,
    referral_record: ReferralRecord,
    amount: Decimal,
    admin_user_id: int,
    paid_at: datetime | None = None,
) -> PaymentRecord:
    return await _create_payment_record(
        db=db,
        admin_user_id=admin_user_id,
        user_id=int(referral_record.referrer_user_id),
        payment_type=PAYMENT_TYPE_REFERRAL_REWARD,
        amount=amount,
        paid_at=paid_at,
        currency="USD",
        referral_record_id=int(referral_record.id),
        remark="Referral reward payout.",
    )


async def list_payment_records_for_admin(
    *,
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    payment_type: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    conditions = [PaymentRecord.is_deleted.is_(False)]
    if payment_type:
        conditions.append(PaymentRecord.payment_type == _safe_normalize_payment_type(payment_type))
    if user_id is not None:
        conditions.append(PaymentRecord.user_id == user_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PaymentRecord.user_snapshot_name.ilike(like),
                PaymentRecord.user_snapshot_email.ilike(like),
                PaymentRecord.company_snapshot_name.ilike(like),
                PaymentRecord.project_snapshot_name.ilike(like),
                PaymentRecord.contract_snapshot_ref_no.ilike(like),
                PaymentRecord.referral_referred_snapshot_name.ilike(like),
                PaymentRecord.referral_referred_snapshot_email.ilike(like),
                PaymentRecord.external_transaction_no.ilike(like),
            )
        )

    total_result = await db.execute(select(func.count()).select_from(PaymentRecord).where(*conditions))
    total = int(total_result.scalar() or 0)
    result = await db.execute(
        select(PaymentRecord)
        .where(*conditions)
        .order_by(PaymentRecord.paid_at.desc(), PaymentRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = result.scalars().all()
    return PaymentRecordListPage(
        items=[_serialize_payment_record(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()


async def list_payment_records_for_candidate(
    *,
    db: AsyncSession,
    user_id: int,
    page: int,
    page_size: int,
    month: str | None = None,
    payment_type: str | None = None,
) -> dict[str, Any]:
    selected_month, month_start, month_end = _parse_month_range(month)
    base_conditions = [
        PaymentRecord.user_id == user_id,
        PaymentRecord.is_deleted.is_(False),
    ]

    list_conditions = [*base_conditions]
    if payment_type:
        list_conditions.append(PaymentRecord.payment_type == _safe_normalize_payment_type(payment_type))
    if month:
        list_conditions.extend(
            [
                PaymentRecord.paid_at >= month_start,
                PaymentRecord.paid_at < month_end,
            ]
        )

    total_paid_result = await db.execute(
        select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(*base_conditions)
    )
    month_paid_result = await db.execute(
        select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
            *base_conditions,
            PaymentRecord.paid_at >= month_start,
            PaymentRecord.paid_at < month_end,
        )
    )
    referral_paid_result = await db.execute(
        select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
            *base_conditions,
            PaymentRecord.payment_type == PAYMENT_TYPE_REFERRAL_REWARD,
        )
    )
    latest_payment_result = await db.execute(select(func.max(PaymentRecord.paid_at)).where(*base_conditions))

    total_result = await db.execute(select(func.count()).select_from(PaymentRecord).where(*list_conditions))
    total = int(total_result.scalar() or 0)
    result = await db.execute(
        select(PaymentRecord)
        .where(*list_conditions)
        .order_by(PaymentRecord.paid_at.desc(), PaymentRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = result.scalars().all()

    return CandidateEarningsListPage(
        summary=CandidateEarningsSummaryRead(
            total_paid=quantize_money(total_paid_result.scalar()),
            month_paid=quantize_money(month_paid_result.scalar()),
            referral_rewards_paid=quantize_money(referral_paid_result.scalar()),
            latest_payment_at=latest_payment_result.scalar(),
            currency="USD",
            month=selected_month,
        ),
        items=[_serialize_candidate_earning_record(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()


async def _load_referral_payable_options(
    *,
    db: AsyncSession,
    limit: int = 100,
) -> list[PaymentRecordReferralOptionRead]:
    referrer_alias = aliased(User)
    referred_alias = aliased(User)
    result = await db.execute(
        select(ReferralRecord, referrer_alias, referred_alias)
        .outerjoin(referrer_alias, referrer_alias.id == ReferralRecord.referrer_user_id)
        .outerjoin(referred_alias, referred_alias.id == ReferralRecord.referred_user_id)
        .where(ReferralRecord.is_deleted.is_(False))
        .order_by(ReferralRecord.updated_at.desc(), ReferralRecord.id.desc())
        .limit(limit)
    )
    rows = result.all()
    referred_user_ids = [int(record.referred_user_id) for record, _, _ in rows]
    if not referred_user_ids:
        return []

    work_result = await db.execute(
        select(
            ProjectTimesheetRecord.user_id,
            func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
        )
        .where(
            ProjectTimesheetRecord.user_id.in_(referred_user_ids),
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
        .group_by(ProjectTimesheetRecord.user_id)
    )
    work_hours_by_user_id = {
        int(user_id): quantize_decimal(work_hours)
        for user_id, work_hours in work_result.all()
    }

    options: list[PaymentRecordReferralOptionRead] = []
    for record, referrer, referred in rows:
        reward_amount = calculate_referral_reward(work_hours_by_user_id.get(int(record.referred_user_id)))
        payable_amount = max(reward_amount - quantize_decimal(record.paid_reward_amount), Decimal("0.00"))
        if payable_amount <= 0:
            continue
        options.append(
            PaymentRecordReferralOptionRead(
                referral_record_id=record.id,
                referrer_user_id=record.referrer_user_id,
                referrer_name=_user_display_name(referrer, record.referrer_snapshot_name),
                referrer_email=referrer.email if referrer is not None else record.referrer_snapshot_email,
                referred_user_id=record.referred_user_id,
                referred_name=_user_display_name(referred, record.referred_snapshot_name),
                referred_email=referred.email if referred is not None else record.referred_snapshot_email,
                payable_reward_amount=payable_amount,
            )
        )
    return options


async def get_payment_record_options_for_admin(*, db: AsyncSession) -> dict[str, Any]:
    user_result = await db.execute(
        select(User, TalentProfile)
        .outerjoin(TalentProfile, TalentProfile.user_id == User.id)
        .where(User.is_deleted.is_(False))
        .order_by(User.name.asc(), User.id.asc())
        .limit(500)
    )
    users = [
        PaymentRecordUserOptionRead(
            user_id=user.id,
            talent_profile_id=talent.id if talent is not None and not talent.is_deleted else None,
            name=_user_display_name(user),
            email=user.email,
        )
        for user, talent in user_result.all()
    ]

    contract_result = await db.execute(
        select(ContractRecord, AdminCompany, AdminCompanyProject)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
        .where(
            ContractRecord.is_deleted.is_(False),
        )
        .order_by(ContractRecord.is_current.desc(), ContractRecord.updated_at.desc(), ContractRecord.id.desc())
        .limit(1000)
    )
    contracts = [
        PaymentRecordContractOptionRead(
            contract_record_id=contract.id,
            user_id=contract.user_id,
            agreement_ref_no=contract.agreement_ref_no,
            job_title=contract.job_snapshot_title,
            company_id=contract.service_customer_company_id,
            company_name=company.name if company is not None else None,
            project_id=contract.service_customer_project_id,
            project_name=project.name if project is not None else None,
            contract_status=contract.contract_status,
        )
        for contract, company, project in contract_result.all()
    ]

    return PaymentRecordOptionsRead(
        users=users,
        contracts=contracts,
        referrals=await _load_referral_payable_options(db=db),
    ).model_dump()
