from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..contract_record.const import CONTRACT_STATUS_ACTIVE, CONTRACT_TYPE_TEAM_LEADER
from ..contract_record.model import ContractRecord
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..project_timesheet_record.team_leader_bonus import calculate_team_leader_bonus
from ..referral.const import (
    REFERRAL_STATUS_PAID,
    REFERRAL_STATUS_READY_TO_PAY,
    REFERRAL_STATUS_TRACKING,
    quantize_decimal,
)
from ..referral.model import ReferralRecord
from ..referral_bonus_model.service import calculate_referral_reward_from_record
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .const import (
    PAYMENT_PAYOUT_STATUS_PAID,
    PAYMENT_PAYOUT_STATUS_PENDING,
    PAYMENT_SOURCE_AUTO_PAYABLE,
    PAYMENT_TYPE_REFERRAL_REWARD,
    PAYMENT_TYPE_SALARY,
    PAYMENT_TYPE_TEAM_LEADER_BONUS,
    normalize_payment_payout_status,
    normalize_payment_type,
    quantize_money,
)
from .model import PaymentRecord
from .schema import (
    CandidateEarningsListPage,
    CandidateEarningsRecordRead,
    CandidateEarningsSummaryRead,
    PaymentPayableListPage,
    PaymentPayableMarkPaidRequest,
    PaymentPayableRecordRead,
    PaymentPayableSummaryRead,
    PaymentPayableUpdateRequest,
    PaymentRecordBatchCreateRequest,
    PaymentRecordContractOptionRead,
    PaymentRecordListPage,
    PaymentRecordOptionsRead,
    PaymentRecordRead,
    PaymentRecordReferralOptionRead,
    PaymentRecordUserOptionRead,
)

AUTO_PAYABLE_CALCULATION_DATA_KEY = "calculation"
AUTO_PAYABLE_SOURCE_KEY_DATA_KEY = "source_key"
AUTO_PAYABLE_SOURCE_MONTH_DATA_KEY = "source_month"
AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY = "payment_source"
AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY = "payout_status"


@dataclass
class _CalculatedPayable:
    source_key: str
    source_month: str
    payment_type: str
    user_id: int
    talent_profile_id: int | None
    contract_record_id: int | None
    referral_record_id: int | None
    amount: Decimal
    currency: str
    user_name: str | None
    user_email: str | None
    company_id: int | None
    project_id: int | None
    company_name: str | None
    project_name: str | None
    contract_ref_no: str | None
    country: str | None
    language: str | None
    work_hours: Decimal
    rate: Decimal | None
    bonus_multiplier: Decimal | None
    source_record_count: int


TeamLeaderContractContext = tuple[
    ContractRecord,
    User,
    TalentProfile | None,
    AdminCompany | None,
    AdminCompanyProject | None,
]


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


def _safe_normalize_payout_status(value: str | None) -> str:
    try:
        return normalize_payment_payout_status(value)
    except ValueError as exc:
        raise BadRequestException("Invalid payout status.") from exc


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


def _parse_payable_month(month: str | None) -> tuple[str, date]:
    now = datetime.now(UTC).date()
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
    return f"{year:04d}-{month_number:02d}", date(year, month_number, 1)


def _next_month_start(month: str) -> date:
    try:
        raw_year, raw_month = month.split("-", 1)
        year = int(raw_year)
        month_number = int(raw_month)
    except Exception as exc:
        raise BadRequestException("Invalid payable source key.") from exc
    if month_number < 1 or month_number > 12:
        raise BadRequestException("Invalid payable source key.")
    if month_number == 12:
        return date(year + 1, 1, 1)
    return date(year, month_number + 1, 1)


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _normalize_decimal(value: Any) -> Decimal:
    return quantize_money(value)


def _merge_label_values(*values: str | None) -> str | None:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for item in str(value).replace("，", ",").split(","):
            label = item.strip()
            key = label.casefold()
            if label and key not in seen:
                seen.add(key)
                normalized.append(label)
    return ", ".join(normalized) if normalized else None


def _talent_language_label(talent: TalentProfile | None) -> str | None:
    if talent is None:
        return None
    return _merge_label_values(talent.native_languages, talent.additional_languages)


def _payable_sort_key(item: PaymentPayableRecordRead) -> tuple[int, str, str, str, int]:
    return (
        1 if item.payout_status == PAYMENT_PAYOUT_STATUS_PENDING else 0,
        item.source_month,
        item.user_name or "",
        item.payment_type,
        item.payment_record_id or 0,
    )


PAYABLE_SORT_FIELDS = {
    "sourceMonth",
    "userName",
    "paymentType",
    "companyName",
    "contractRefNo",
    "workHours",
    "amount",
    "payoutStatus",
    "paidAt",
    "externalTransactionNo",
    "remark",
}


def _safe_normalize_payable_sort_by(value: str | None) -> str | None:
    sort_by = (value or "").strip()
    if not sort_by:
        return None
    if sort_by not in PAYABLE_SORT_FIELDS:
        raise BadRequestException("Invalid payable sort field.")
    return sort_by


def _safe_normalize_payable_sort_order(value: str | None) -> str:
    sort_order = (value or "ascend").strip().casefold()
    if sort_order in ("asc", "ascend"):
        return "ascend"
    if sort_order in ("desc", "descend"):
        return "descend"
    raise BadRequestException("Invalid payable sort order.")


def _payable_text_sort_value(value: str | None) -> str:
    return (value or "").casefold()


def _payable_datetime_sort_value(value: datetime | None) -> float:
    return value.timestamp() if value is not None else float("-inf")


def _payable_sort_value(item: PaymentPayableRecordRead, sort_by: str) -> tuple[Any, ...]:
    if sort_by == "sourceMonth":
        return (item.source_month,)
    if sort_by == "userName":
        return (_payable_text_sort_value(item.user_name), _payable_text_sort_value(item.user_email))
    if sort_by == "paymentType":
        return (_payable_text_sort_value(item.payment_type),)
    if sort_by == "companyName":
        return (_payable_text_sort_value(item.company_name), _payable_text_sort_value(item.project_name))
    if sort_by == "contractRefNo":
        return (_payable_text_sort_value(item.contract_ref_no),)
    if sort_by == "workHours":
        return (item.work_hours,)
    if sort_by == "amount":
        return (item.amount,)
    if sort_by == "payoutStatus":
        return (_payable_text_sort_value(item.payout_status),)
    if sort_by == "paidAt":
        return (_payable_datetime_sort_value(item.paid_at),)
    if sort_by == "externalTransactionNo":
        return (
            _payable_text_sort_value(item.external_platform),
            _payable_text_sort_value(item.external_transaction_no),
        )
    if sort_by == "remark":
        return (_payable_text_sort_value(item.remark),)
    return _payable_sort_key(item)


def _sort_payables(
    items: list[PaymentPayableRecordRead],
    *,
    sort_by: str | None,
    sort_order: str | None,
) -> None:
    normalized_sort_by = _safe_normalize_payable_sort_by(sort_by)
    if normalized_sort_by is None:
        items.sort(key=_payable_sort_key)
        items.reverse()
        return
    normalized_sort_order = _safe_normalize_payable_sort_order(sort_order)
    items.sort(
        key=lambda item: (_payable_sort_value(item, normalized_sort_by), _payable_sort_key(item)),
        reverse=normalized_sort_order == "descend",
    )


def _build_auto_payable_data(
    item: _CalculatedPayable,
    *,
    payout_status: str = PAYMENT_PAYOUT_STATUS_PAID,
) -> dict[str, Any]:
    calculation: dict[str, Any] = {
        "work_hours": str(_normalize_decimal(item.work_hours)),
        "source_record_count": int(item.source_record_count),
    }
    if item.rate is not None:
        calculation["rate"] = str(_normalize_decimal(item.rate))
    if item.bonus_multiplier is not None:
        calculation["bonus_multiplier"] = str(_normalize_decimal(item.bonus_multiplier))
    if item.country:
        calculation["country"] = item.country
    if item.language:
        calculation["language"] = item.language
    return {
        AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY: PAYMENT_SOURCE_AUTO_PAYABLE,
        AUTO_PAYABLE_SOURCE_KEY_DATA_KEY: item.source_key,
        AUTO_PAYABLE_SOURCE_MONTH_DATA_KEY: item.source_month,
        AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY: payout_status,
        AUTO_PAYABLE_CALCULATION_DATA_KEY: calculation,
    }


async def _sync_referral_reward_status(
    *,
    db: AsyncSession,
    record: PaymentRecord,
    previous_status: str,
    next_status: str,
    admin_user_id: int,
) -> None:
    if record.payment_type != PAYMENT_TYPE_REFERRAL_REWARD or record.referral_record_id is None:
        return
    if previous_status == next_status:
        return

    referral = await db.get(ReferralRecord, int(record.referral_record_id))
    if referral is None or referral.is_deleted:
        return

    amount = quantize_decimal(record.amount)
    paid_amount = quantize_decimal(referral.paid_reward_amount)
    if previous_status != PAYMENT_PAYOUT_STATUS_PAID and next_status == PAYMENT_PAYOUT_STATUS_PAID:
        paid_amount = quantize_decimal(paid_amount + amount)
    elif previous_status == PAYMENT_PAYOUT_STATUS_PAID and next_status != PAYMENT_PAYOUT_STATUS_PAID:
        paid_amount = max(quantize_decimal(paid_amount - amount), Decimal("0.00"))
    else:
        return

    referral.paid_reward_amount = paid_amount
    referral.payout_status = (
        REFERRAL_STATUS_PAID
        if next_status == PAYMENT_PAYOUT_STATUS_PAID and paid_amount > 0
        else (REFERRAL_STATUS_READY_TO_PAY if paid_amount > 0 else REFERRAL_STATUS_TRACKING)
    )
    referral.last_paid_at = record.paid_at if next_status == PAYMENT_PAYOUT_STATUS_PAID else referral.last_paid_at
    referral.last_paid_by_admin_user_id = (
        admin_user_id if next_status == PAYMENT_PAYOUT_STATUS_PAID else referral.last_paid_by_admin_user_id
    )


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
    data: dict[str, Any] | None = None,
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
        data=data or {},
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
        currency=referral_record.currency,
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


def _serialize_calculated_payable(item: _CalculatedPayable) -> PaymentPayableRecordRead:
    return PaymentPayableRecordRead(
        id=f"pending:{item.source_key}",
        source_key=item.source_key,
        source_month=item.source_month,
        payout_status=PAYMENT_PAYOUT_STATUS_PENDING,
        payment_record_id=None,
        user_id=item.user_id,
        talent_profile_id=item.talent_profile_id,
        contract_record_id=item.contract_record_id,
        referral_record_id=item.referral_record_id,
        payment_type=item.payment_type,
        amount=_normalize_decimal(item.amount),
        currency=item.currency,
        paid_at=None,
        external_platform=None,
        external_transaction_no=None,
        remark=None,
        user_name=item.user_name,
        user_email=item.user_email,
        company_id=item.company_id,
        project_id=item.project_id,
        company_name=item.company_name,
        project_name=item.project_name,
        contract_ref_no=item.contract_ref_no,
        country=item.country,
        language=item.language,
        work_hours=_normalize_decimal(item.work_hours),
        rate=_normalize_decimal(item.rate) if item.rate is not None else None,
        bonus_multiplier=_normalize_decimal(item.bonus_multiplier) if item.bonus_multiplier is not None else None,
        source_record_count=item.source_record_count,
        created_at=None,
        updated_at=None,
    )


def _serialize_paid_payable_record(record: PaymentRecord) -> PaymentPayableRecordRead | None:
    data = record.data or {}
    has_auto_payable_data = data.get(AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY) == PAYMENT_SOURCE_AUTO_PAYABLE
    if not has_auto_payable_data and record.payment_type != PAYMENT_TYPE_REFERRAL_REWARD:
        return None
    source_month = str(data.get(AUTO_PAYABLE_SOURCE_MONTH_DATA_KEY) or "").strip()
    if not source_month and record.paid_at:
        source_month = _month_key(record.paid_at.date())
    source_key = str(data.get(AUTO_PAYABLE_SOURCE_KEY_DATA_KEY) or "").strip()
    if not source_key and record.payment_type == PAYMENT_TYPE_REFERRAL_REWARD and record.referral_record_id:
        source_key = _build_referral_reward_source_key(
            source_month,
            int(record.referral_record_id),
            _normalize_decimal(record.amount),
        )
    if not source_key or not source_month:
        return None
    calculation = data.get(AUTO_PAYABLE_CALCULATION_DATA_KEY)
    if not isinstance(calculation, dict):
        calculation = {}
    rate = calculation.get("rate")
    bonus_multiplier = calculation.get("bonus_multiplier")
    country = str(calculation.get("country") or "").strip() or None
    language = str(calculation.get("language") or "").strip() or None
    payout_status = str(data.get(AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY) or PAYMENT_PAYOUT_STATUS_PAID).strip()
    try:
        payout_status = normalize_payment_payout_status(payout_status)
    except ValueError:
        payout_status = PAYMENT_PAYOUT_STATUS_PAID
    return PaymentPayableRecordRead(
        id=f"paid:{record.id}",
        source_key=source_key,
        source_month=source_month,
        payout_status=payout_status,
        payment_record_id=record.id,
        user_id=record.user_id,
        talent_profile_id=record.talent_profile_id,
        contract_record_id=record.contract_record_id,
        referral_record_id=record.referral_record_id,
        payment_type=record.payment_type,
        amount=_normalize_decimal(record.amount),
        currency=record.currency,
        paid_at=record.paid_at if payout_status == PAYMENT_PAYOUT_STATUS_PAID else None,
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
        country=country,
        language=language,
        work_hours=_normalize_decimal(calculation.get("work_hours")),
        rate=_normalize_decimal(rate) if rate not in (None, "") else None,
        bonus_multiplier=_normalize_decimal(bonus_multiplier) if bonus_multiplier not in (None, "") else None,
        source_record_count=int(calculation.get("source_record_count") or 0),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _load_paid_auto_payables(
    *,
    db: AsyncSession,
    source_month: str | None = None,
    payment_type: str | None = None,
) -> dict[str, PaymentPayableRecordRead]:
    conditions = [
        PaymentRecord.is_deleted.is_(False),
        PaymentRecord.payment_type.in_(
            [PAYMENT_TYPE_SALARY, PAYMENT_TYPE_TEAM_LEADER_BONUS, PAYMENT_TYPE_REFERRAL_REWARD]
        ),
    ]
    if payment_type:
        conditions.append(PaymentRecord.payment_type == _safe_normalize_payment_type(payment_type))
    result = await db.execute(
        select(PaymentRecord)
        .where(*conditions)
        .order_by(PaymentRecord.paid_at.desc(), PaymentRecord.id.desc())
    )
    records_by_source_key: dict[str, PaymentPayableRecordRead] = {}
    for record in result.scalars().all():
        item = _serialize_paid_payable_record(record)
        if item is None:
            continue
        if source_month and item.source_month != source_month:
            continue
        records_by_source_key.setdefault(item.source_key, item)
    return records_by_source_key


def _build_salary_source_key(source_month: str, contract_record_id: int) -> str:
    return f"{PAYMENT_TYPE_SALARY}:{source_month}:{contract_record_id}"


def _build_team_leader_bonus_source_key(source_month: str, leader_user_id: int) -> str:
    return f"{PAYMENT_TYPE_TEAM_LEADER_BONUS}:{source_month}:{leader_user_id}"


def _build_referral_reward_source_key(source_month: str, referral_record_id: int, referral_earnings: Decimal) -> str:
    return f"{PAYMENT_TYPE_REFERRAL_REWARD}:{source_month}:{referral_record_id}:{_normalize_decimal(referral_earnings)}"


def _referral_reward_target_hours(record: ReferralRecord, work_hours: Decimal) -> Decimal:
    milestones = list((record.data or {}).get("milestones") or [])
    reached_hours = [
        quantize_decimal(milestone.get("required_hours"))
        for milestone in milestones
        if work_hours >= quantize_decimal(milestone.get("required_hours"))
    ]
    if not reached_hours:
        return work_hours
    return max(reached_hours)


def _referral_reward_source_month(
    rows: list[ProjectTimesheetRecord],
    *,
    target_hours: Decimal,
) -> str:
    cumulative = Decimal("0.00")
    fallback = rows[-1].work_date if rows else datetime.now(UTC).date()
    for row in rows:
        hours = _normalize_decimal(row.candidate_duration_hours)
        if hours <= 0:
            continue
        cumulative = _normalize_decimal(cumulative + hours)
        if cumulative >= target_hours:
            return _month_key(row.work_date)
    return _month_key(fallback)


async def _calculate_salary_payables(*, db: AsyncSession, cutoff_start: date) -> list[_CalculatedPayable]:
    result = await db.execute(
        select(ProjectTimesheetRecord, ContractRecord, User, TalentProfile, AdminCompany, AdminCompanyProject)
        .join(ContractRecord, ContractRecord.id == ProjectTimesheetRecord.contract_record_id)
        .join(User, User.id == ProjectTimesheetRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.id == ProjectTimesheetRecord.talent_profile_id)
        .outerjoin(AdminCompany, AdminCompany.id == ProjectTimesheetRecord.company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ProjectTimesheetRecord.project_id)
        .where(
            ProjectTimesheetRecord.work_date < cutoff_start,
            ProjectTimesheetRecord.is_deleted.is_(False),
            ContractRecord.is_deleted.is_(False),
            User.is_deleted.is_(False),
        )
        .order_by(ProjectTimesheetRecord.work_date.asc(), ProjectTimesheetRecord.id.asc())
    )
    groups: dict[str, _CalculatedPayable] = {}
    for record, contract, user, talent, company, project in result.all():
        hours = _normalize_decimal(record.candidate_duration_hours)
        if hours <= 0:
            continue
        rate = _normalize_decimal(contract.rate)
        if rate <= 0:
            continue
        source_month = _month_key(record.work_date)
        source_key = _build_salary_source_key(source_month, int(contract.id))
        group = groups.get(source_key)
        if group is None:
            display_name = (
                (talent.full_name if talent is not None and talent.full_name else None)
                or contract.contractor_name
                or record.user_name_snapshot
                or user.name
                or user.email
            )
            group = _CalculatedPayable(
                source_key=source_key,
                source_month=source_month,
                payment_type=PAYMENT_TYPE_SALARY,
                user_id=int(user.id),
                talent_profile_id=record.talent_profile_id or contract.talent_profile_id,
                contract_record_id=int(contract.id),
                referral_record_id=None,
                amount=Decimal("0.00"),
                currency="USD",
                user_name=display_name,
                user_email=record.user_email_snapshot or contract.user_snapshot_email or user.email,
                company_id=record.company_id or contract.service_customer_company_id,
                project_id=record.project_id or contract.service_customer_project_id,
                company_name=company.name if company is not None else None,
                project_name=project.name if project is not None else None,
                contract_ref_no=contract.agreement_ref_no,
                country=(talent.location or talent.nationality) if talent is not None else None,
                language=record.language,
                work_hours=Decimal("0.00"),
                rate=rate,
                bonus_multiplier=None,
                source_record_count=0,
            )
            groups[source_key] = group
        group.work_hours = _normalize_decimal(group.work_hours + hours)
        group.amount = _normalize_decimal(group.work_hours * (group.rate or Decimal("0.00")))
        group.source_record_count += 1
        group.language = _merge_label_values(group.language, record.language)
    return [item for item in groups.values() if item.amount > 0]


async def _load_team_leader_contract_contexts(
    *,
    db: AsyncSession,
    leader_user_ids: list[int],
) -> dict[int, TeamLeaderContractContext]:
    if not leader_user_ids:
        return {}
    result = await db.execute(
        select(ContractRecord, User, TalentProfile, AdminCompany, AdminCompanyProject)
        .join(User, User.id == ContractRecord.user_id)
        .outerjoin(TalentProfile, TalentProfile.user_id == User.id)
        .outerjoin(AdminCompany, AdminCompany.id == ContractRecord.service_customer_company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == ContractRecord.service_customer_project_id)
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
    contexts: dict[int, TeamLeaderContractContext] = {}
    for contract, user, talent, company, project in result.all():
        contexts.setdefault(int(user.id), (contract, user, talent, company, project))
    return contexts


async def _calculate_team_leader_bonus_payables(*, db: AsyncSession, cutoff_start: date) -> list[_CalculatedPayable]:
    result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(
            ProjectTimesheetRecord.work_date < cutoff_start,
            ProjectTimesheetRecord.team_leader_user_id.is_not(None),
            ProjectTimesheetRecord.team_leader_user_id > 0,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
        .order_by(ProjectTimesheetRecord.work_date.asc(), ProjectTimesheetRecord.id.asc())
    )
    rows = result.scalars().all()
    leader_user_ids = sorted(
        {
            int(row.team_leader_user_id)
            for row in rows
            if row.team_leader_user_id is not None and int(row.team_leader_user_id) > 0
        }
    )
    contexts = await _load_team_leader_contract_contexts(db=db, leader_user_ids=leader_user_ids)
    groups: dict[str, _CalculatedPayable] = {}
    for record in rows:
        if record.team_leader_user_id is None or int(record.team_leader_user_id) <= 0:
            continue
        hours = _normalize_decimal(record.candidate_duration_hours)
        if hours <= 0:
            continue
        leader_user_id = int(record.team_leader_user_id)
        context = contexts.get(leader_user_id)
        if context is None:
            continue
        contract, user, talent, company, project = context
        source_month = _month_key(record.work_date)
        source_key = _build_team_leader_bonus_source_key(source_month, leader_user_id)
        group = groups.get(source_key)
        if group is None:
            display_name = (
                (talent.full_name if talent is not None and talent.full_name else None)
                or contract.contractor_name
                or contract.user_snapshot_name
                or user.name
                or user.email
            )
            group = _CalculatedPayable(
                source_key=source_key,
                source_month=source_month,
                payment_type=PAYMENT_TYPE_TEAM_LEADER_BONUS,
                user_id=leader_user_id,
                talent_profile_id=contract.talent_profile_id,
                contract_record_id=int(contract.id),
                referral_record_id=None,
                amount=Decimal("0.00"),
                currency="USD",
                user_name=display_name,
                user_email=contract.user_snapshot_email or user.email,
                company_id=contract.service_customer_company_id,
                project_id=contract.service_customer_project_id,
                company_name=company.name if company is not None else None,
                project_name=project.name if project is not None else None,
                contract_ref_no=contract.agreement_ref_no,
                country=(talent.location or talent.nationality) if talent is not None else None,
                language=record.language or _talent_language_label(talent),
                work_hours=Decimal("0.00"),
                rate=None,
                bonus_multiplier=None,
                source_record_count=0,
            )
            groups[source_key] = group
        group.work_hours = _normalize_decimal(group.work_hours + hours)
        multiplier, bonus = calculate_team_leader_bonus(group.work_hours)
        group.bonus_multiplier = multiplier
        group.amount = bonus
        group.source_record_count += 1
        group.language = _merge_label_values(group.language, record.language)
    return [item for item in groups.values() if item.amount > 0]


async def _calculate_referral_reward_payables(*, db: AsyncSession, cutoff_start: date) -> list[_CalculatedPayable]:
    referrer_alias = aliased(User)
    referred_alias = aliased(User)
    referrer_talent_alias = aliased(TalentProfile)
    referral_result = await db.execute(
        select(ReferralRecord, referrer_alias, referred_alias, referrer_talent_alias)
        .outerjoin(referrer_alias, referrer_alias.id == ReferralRecord.referrer_user_id)
        .outerjoin(referred_alias, referred_alias.id == ReferralRecord.referred_user_id)
        .outerjoin(referrer_talent_alias, referrer_talent_alias.user_id == ReferralRecord.referrer_user_id)
        .where(ReferralRecord.is_deleted.is_(False))
        .order_by(ReferralRecord.id.asc())
    )
    referral_rows = referral_result.all()
    referred_user_ids = [int(record.referred_user_id) for record, _, _, _ in referral_rows]
    if not referred_user_ids:
        return []

    work_result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(
            ProjectTimesheetRecord.user_id.in_(referred_user_ids),
            ProjectTimesheetRecord.work_date < cutoff_start,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
        .order_by(ProjectTimesheetRecord.work_date.asc(), ProjectTimesheetRecord.id.asc())
    )
    work_rows_by_user_id: dict[int, list[ProjectTimesheetRecord]] = {}
    for row in work_result.scalars().all():
        work_rows_by_user_id.setdefault(int(row.user_id), []).append(row)

    items: list[_CalculatedPayable] = []
    for record, referrer, referred, referrer_talent in referral_rows:
        work_rows = work_rows_by_user_id.get(int(record.referred_user_id), [])
        work_hours = _normalize_decimal(
            sum((_normalize_decimal(row.candidate_duration_hours) for row in work_rows), Decimal("0.00"))
        )
        if work_hours <= 0:
            continue
        referral_earnings = calculate_referral_reward_from_record(record, work_hours)
        payable_amount = max(referral_earnings - quantize_decimal(record.paid_reward_amount), Decimal("0.00"))
        payable_amount = _normalize_decimal(payable_amount)
        if payable_amount <= 0:
            continue
        target_hours = _referral_reward_target_hours(record, work_hours)
        source_month = _referral_reward_source_month(work_rows, target_hours=target_hours)
        display_name = (
            (referrer_talent.full_name if referrer_talent is not None and referrer_talent.full_name else None)
            or (referrer.name if referrer is not None else None)
            or record.referrer_snapshot_name
            or (referrer.email if referrer is not None else None)
            or record.referrer_snapshot_email
        )
        items.append(
            _CalculatedPayable(
                source_key=_build_referral_reward_source_key(source_month, int(record.id), referral_earnings),
                source_month=source_month,
                payment_type=PAYMENT_TYPE_REFERRAL_REWARD,
                user_id=int(record.referrer_user_id),
                talent_profile_id=referrer_talent.id if referrer_talent is not None else None,
                contract_record_id=None,
                referral_record_id=int(record.id),
                amount=payable_amount,
                currency=record.currency,
                user_name=display_name,
                user_email=(referrer.email if referrer is not None else None) or record.referrer_snapshot_email,
                company_id=None,
                project_id=None,
                company_name=None,
                project_name=None,
                contract_ref_no=f"Referral: {referred.name or referred.email}" if referred is not None else "Referral",
                country=(
                    referrer_talent.location or referrer_talent.nationality
                    if referrer_talent is not None
                    else None
                ),
                language=_talent_language_label(referrer_talent),
                work_hours=work_hours,
                rate=None,
                bonus_multiplier=None,
                source_record_count=len(work_rows),
            )
        )
    return items


async def _calculate_auto_payables(
    *,
    db: AsyncSession,
    cutoff_start: date,
    payment_type: str | None = None,
) -> list[_CalculatedPayable]:
    normalized_payment_type = _safe_normalize_payment_type(payment_type) if payment_type else None
    items: list[_CalculatedPayable] = []
    if normalized_payment_type in (None, PAYMENT_TYPE_SALARY):
        items.extend(await _calculate_salary_payables(db=db, cutoff_start=cutoff_start))
    if normalized_payment_type in (None, PAYMENT_TYPE_TEAM_LEADER_BONUS):
        items.extend(await _calculate_team_leader_bonus_payables(db=db, cutoff_start=cutoff_start))
    if normalized_payment_type in (None, PAYMENT_TYPE_REFERRAL_REWARD):
        items.extend(await _calculate_referral_reward_payables(db=db, cutoff_start=cutoff_start))
    return items


def _payable_matches_keyword(item: PaymentPayableRecordRead, keyword: str | None) -> bool:
    text = (keyword or "").strip().casefold()
    if not text:
        return True
    haystack = " ".join(
        str(value or "")
        for value in (
            item.source_month,
            item.user_name,
            item.user_email,
            item.company_name,
            item.project_name,
            item.contract_ref_no,
            item.external_platform,
            item.external_transaction_no,
            item.remark,
            item.country,
            item.language,
        )
    ).casefold()
    return text in haystack


def _build_payable_summary(*, month: str, items: list[PaymentPayableRecordRead]) -> PaymentPayableSummaryRead:
    pending_items = [item for item in items if item.payout_status != PAYMENT_PAYOUT_STATUS_PAID]
    paid_items = [item for item in items if item.payout_status == PAYMENT_PAYOUT_STATUS_PAID]
    pending_amount = sum((item.amount for item in pending_items), Decimal("0.00"))
    paid_amount = sum((item.amount for item in paid_items), Decimal("0.00"))
    return PaymentPayableSummaryRead(
        month=month,
        pending_count=len(pending_items),
        paid_count=len(paid_items),
        pending_amount=_normalize_decimal(pending_amount),
        paid_amount=_normalize_decimal(paid_amount),
        total_amount=_normalize_decimal(pending_amount + paid_amount),
        currency="USD",
    )


async def list_auto_payment_payables_for_admin(
    *,
    db: AsyncSession,
    page: int,
    page_size: int,
    month: str | None = None,
    keyword: str | None = None,
    payment_type: str | None = None,
    payout_status: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> dict[str, Any]:
    payable_month, _month_start = _parse_payable_month(month)
    cutoff_start = _next_month_start(payable_month)
    normalized_payment_type = _safe_normalize_payment_type(payment_type) if payment_type else None
    normalized_payout_status = _safe_normalize_payout_status(payout_status) if payout_status else None
    all_recorded_by_source_key = await _load_paid_auto_payables(
        db=db,
        payment_type=normalized_payment_type,
    )
    items: list[PaymentPayableRecordRead] = []
    recorded_items = [
        item
        for item in all_recorded_by_source_key.values()
        if (
            item.source_month == payable_month
            or (item.source_month <= payable_month and item.payout_status != PAYMENT_PAYOUT_STATUS_PAID)
        )
    ]
    if normalized_payout_status is None:
        items.extend(recorded_items)
    else:
        items.extend(item for item in recorded_items if item.payout_status == normalized_payout_status)
    if normalized_payout_status in (None, PAYMENT_PAYOUT_STATUS_PENDING):
        calculated = await _calculate_auto_payables(
            db=db,
            cutoff_start=cutoff_start,
            payment_type=normalized_payment_type,
        )
        items.extend(
            _serialize_calculated_payable(item)
            for item in calculated
            if item.source_month <= payable_month and item.source_key not in all_recorded_by_source_key
        )

    items = [item for item in items if _payable_matches_keyword(item, keyword)]
    _sort_payables(items, sort_by=sort_by, sort_order=sort_order)
    total = len(items)
    offset = (page - 1) * page_size
    return PaymentPayableListPage(
        items=items[offset : offset + page_size],
        total=total,
        page=page,
        page_size=page_size,
        summary=_build_payable_summary(month=payable_month, items=items),
    ).model_dump()


def _cutoff_for_source_keys(source_keys: list[str]) -> date:
    source_months: list[str] = []
    for source_key in source_keys:
        parts = source_key.split(":")
        if len(parts) < 3:
            raise BadRequestException("Invalid payable source key.")
        source_months.append(parts[1])
    return _next_month_start(max(source_months))


async def mark_auto_payment_payables_paid(
    *,
    db: AsyncSession,
    admin_user_id: int,
    payload: PaymentPayableMarkPaidRequest,
) -> dict[str, Any]:
    source_keys = payload.source_keys
    payout_status = _safe_normalize_payout_status(payload.payout_status)
    recorded_by_source_key = await _load_paid_auto_payables(db=db)
    duplicate_keys = [source_key for source_key in source_keys if source_key in recorded_by_source_key]
    if duplicate_keys:
        raise BadRequestException("Selected payable record already has a saved payout status.")

    cutoff_start = _cutoff_for_source_keys(source_keys)
    calculated = await _calculate_auto_payables(db=db, cutoff_start=cutoff_start)
    calculated_by_source_key = {item.source_key: item for item in calculated}
    missing_keys = [source_key for source_key in source_keys if source_key not in calculated_by_source_key]
    if missing_keys:
        raise NotFoundException("Selected payable record was not found.")

    paid_at = payload.paid_at or datetime.now(UTC)
    created: list[PaymentRecord] = []
    for source_key in source_keys:
        item = calculated_by_source_key[source_key]
        record = await _create_payment_record(
            db=db,
            admin_user_id=admin_user_id,
            user_id=item.user_id,
            payment_type=item.payment_type,
            amount=item.amount,
            paid_at=paid_at,
            currency=item.currency,
            contract_record_id=item.contract_record_id,
            referral_record_id=item.referral_record_id,
            external_platform=payload.external_platform,
            external_transaction_no=payload.external_transaction_no,
            remark=payload.remark,
            data=_build_auto_payable_data(item, payout_status=payout_status),
        )
        await _sync_referral_reward_status(
            db=db,
            record=record,
            previous_status=PAYMENT_PAYOUT_STATUS_PENDING,
            next_status=payout_status,
            admin_user_id=admin_user_id,
        )
        created.append(record)

    await create_operation_log(
        db=db,
        user_id=None,
        log_type=OperationLogType.PAYMENT_RECORD_BATCH_CREATED.value,
        data={
            "created_count": len(created),
            "payment_record_ids": [record.id for record in created],
            "source_keys": source_keys,
            "operator_admin_user_id": admin_user_id,
            "payout_status": payout_status,
            "note": "Auto payables payout status updated.",
        },
    )
    items = [_serialize_paid_payable_record(record) for record in created]
    return {
        "items": [item.model_dump() for item in items if item is not None],
        "created_count": len(created),
    }


async def update_auto_payment_payable_info(
    *,
    db: AsyncSession,
    admin_user_id: int,
    payment_record_id: int,
    payload: PaymentPayableUpdateRequest,
) -> dict[str, Any]:
    record = await db.get(PaymentRecord, payment_record_id)
    if record is None or record.is_deleted:
        raise NotFoundException("Payment record not found.")
    if record.payment_type not in (PAYMENT_TYPE_SALARY, PAYMENT_TYPE_TEAM_LEADER_BONUS, PAYMENT_TYPE_REFERRAL_REWARD):
        raise BadRequestException("Only salary, team leader bonus, and referral reward records can be updated here.")
    data = dict(record.data or {})
    if (
        data.get(AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY) != PAYMENT_SOURCE_AUTO_PAYABLE
        and record.payment_type == PAYMENT_TYPE_REFERRAL_REWARD
    ):
        source_month = _month_key(record.paid_at.date())
        data = {
            **data,
            AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY: PAYMENT_SOURCE_AUTO_PAYABLE,
            AUTO_PAYABLE_SOURCE_KEY_DATA_KEY: _build_referral_reward_source_key(
                source_month,
                int(record.referral_record_id or record.id),
                _normalize_decimal(record.amount),
            ),
            AUTO_PAYABLE_SOURCE_MONTH_DATA_KEY: source_month,
            AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY: PAYMENT_PAYOUT_STATUS_PAID,
            AUTO_PAYABLE_CALCULATION_DATA_KEY: {
                "work_hours": "0.00",
                "source_record_count": 1,
            },
        }
    if data.get(AUTO_PAYABLE_SOURCE_TYPE_DATA_KEY) != PAYMENT_SOURCE_AUTO_PAYABLE:
        raise BadRequestException("Only auto-calculated payable records can be updated here.")

    previous_status = str(data.get(AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY) or PAYMENT_PAYOUT_STATUS_PAID)
    try:
        previous_status = normalize_payment_payout_status(previous_status)
    except ValueError:
        previous_status = PAYMENT_PAYOUT_STATUS_PAID
    next_status = previous_status
    if payload.payout_status is not None:
        next_status = _safe_normalize_payout_status(payload.payout_status)
        data[AUTO_PAYABLE_PAYOUT_STATUS_DATA_KEY] = next_status

    if payload.paid_at is not None:
        record.paid_at = payload.paid_at
    record.external_platform = payload.external_platform
    record.external_transaction_no = payload.external_transaction_no
    record.remark = payload.remark
    record.data = data
    record.updated_by_admin_user_id = admin_user_id
    await _sync_referral_reward_status(
        db=db,
        record=record,
        previous_status=previous_status,
        next_status=next_status,
        admin_user_id=admin_user_id,
    )
    await db.flush()
    await db.refresh(record)

    item = _serialize_paid_payable_record(record)
    if item is None:
        raise BadRequestException("Payment record is missing payable metadata.")
    return {"item": item.model_dump()}


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
        reward_amount = calculate_referral_reward_from_record(
            record,
            work_hours_by_user_id.get(int(record.referred_user_id)),
        )
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
