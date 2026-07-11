from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..assets.model import Asset
from ..assets.service import serialize_asset
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_field.const import CandidateFieldKey
from ..contract_record.model import ContractRecord
from ..job_progress.const import JobProgressDataKey, RecruitmentStage
from ..job_progress.model import JobProgress
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..referral.model import ReferralRecord
from ..talent_profile.model import TalentProfile
from ..user.model import User

TALENT_STATUS_REJECTED = "rejected"
TALENT_STATUS_RECRUITING = "recruiting"
TALENT_STATUS_ACTIVE = "active"
TALENT_STATUS_REPLACED = "replaced"
TALENT_STATUS_ON_LEAVE = "on_leave"
TALENT_STATUS_OVERRIDE_KEY = "talent_status_override"

TALENT_STATUS_LABELS = {
    TALENT_STATUS_REJECTED: "淘汰",
    TALENT_STATUS_RECRUITING: "招聘",
    TALENT_STATUS_ACTIVE: "在职",
    TALENT_STATUS_REPLACED: "汰换",
    TALENT_STATUS_ON_LEAVE: "休假",
}

APPLICATION_EXTRA_FIELD_KEYS = {
    CandidateFieldKey.ENGLISH_PROFICIENCY.value,
    CandidateFieldKey.AGE_RANGE.value,
}


@dataclass(slots=True)
class TalentPoolSourceBundle:
    application_fields_by_user: dict[int, dict[str, str]]
    progress_by_talent: dict[int, JobProgress]
    contract_by_talent: dict[int, ContractRecord]
    referrer_name_by_user: dict[int, str]
    total_hours_by_talent: dict[int, Decimal]
    recent_work_date_by_talent: dict[int, date]
    asset_by_id: dict[int, Asset]
    id_attachment_asset_id_by_user: dict[int, int]


def normalize_display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, list):
        flattened = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(flattened) if flattened else None
    if isinstance(value, int | float | bool | Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def parse_iso_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    normalized = normalize_display_value(value)
    if normalized is None:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    normalized = normalize_display_value(value)
    if normalized is None:
        return None
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _extract_id_attachment_asset_id(user_data: dict[str, Any] | None) -> int | None:
    payment_info = (user_data or {}).get("payment_info")
    if not isinstance(payment_info, dict):
        return None
    raw_asset_id = payment_info.get("id_attachment_asset_id")
    if raw_asset_id is None or raw_asset_id == "" or raw_asset_id == 0:
        return None
    try:
        return int(raw_asset_id)
    except (TypeError, ValueError):
        return None


def serialize_talent_attachment(asset: Asset | None) -> dict[str, Any] | None:
    if asset is None or asset.is_deleted:
        return None
    payload = serialize_asset(asset)
    return {
        "asset_id": int(payload["id"]),
        "name": str(payload["original_name"]),
        "preview_url": payload.get("preview_url"),
        "download_url": payload.get("download_url"),
        "mime_type": payload.get("mime_type"),
    }


def _progress_has_pool_data(progress: JobProgress) -> bool:
    data = progress.data or {}
    return any(
        normalize_display_value(data.get(key.value))
        for key in (
            JobProgressDataKey.JOB_LANGUAGES,
            JobProgressDataKey.ONBOARDING_STATUS,
            JobProgressDataKey.ONBOARDING_DATE,
            JobProgressDataKey.ACCEPTED_RATE,
            JobProgressDataKey.CONTRACT_NUMBER,
            JobProgressDataKey.NOTE,
        )
    )


def _choose_progress_by_talent(progress_rows: Sequence[JobProgress]) -> dict[int, JobProgress]:
    result: dict[int, JobProgress] = {}
    for progress in sorted(
        progress_rows,
        key=lambda item: (
            1 if _progress_has_pool_data(item) else 0,
            item.updated_at or item.entered_stage_at,
            item.entered_stage_at,
            item.id,
        ),
        reverse=True,
    ):
        if progress.talent_profile_id is None:
            continue
        result.setdefault(int(progress.talent_profile_id), progress)
    return result


def _choose_contract_by_talent(contract_rows: Sequence[ContractRecord]) -> dict[int, ContractRecord]:
    result: dict[int, ContractRecord] = {}
    for contract in sorted(
        contract_rows,
        key=lambda item: (item.updated_at or item.created_at, item.id),
        reverse=True,
    ):
        if contract.talent_profile_id is None:
            continue
        result.setdefault(int(contract.talent_profile_id), contract)
    return result


async def _load_application_extra_fields(
    *,
    db: AsyncSession,
    user_ids: set[int],
) -> dict[int, dict[str, str]]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(CandidateApplication.user_id, CandidateApplicationFieldValue)
        .join(CandidateApplicationFieldValue, CandidateApplicationFieldValue.application_id == CandidateApplication.id)
        .where(
            CandidateApplication.user_id.in_(sorted(user_ids)),
            CandidateApplication.is_deleted.is_(False),
            CandidateApplicationFieldValue.catalog_key.in_(sorted(APPLICATION_EXTRA_FIELD_KEYS)),
        )
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
    )
    fields_by_user: dict[int, dict[str, str]] = {}
    for user_id, field_row in result.all():
        catalog_key = field_row.catalog_key or field_row.field_key
        display_value = normalize_display_value(field_row.display_value or field_row.raw_value)
        if display_value is None:
            continue
        fields_by_user.setdefault(int(user_id), {}).setdefault(catalog_key, display_value)
    return fields_by_user


async def _load_referrers(
    *,
    db: AsyncSession,
    user_ids: set[int],
) -> dict[int, str]:
    if not user_ids:
        return {}
    referrer_user = User.__table__.alias("referrer_user")
    result = await db.execute(
        select(
            ReferralRecord.referred_user_id,
            ReferralRecord.referrer_snapshot_name,
            referrer_user.c.name.label("referrer_user_name"),
            referrer_user.c.email.label("referrer_user_email"),
        )
        .outerjoin(referrer_user, referrer_user.c.id == ReferralRecord.referrer_user_id)
        .where(
            ReferralRecord.referred_user_id.in_(sorted(user_ids)),
            ReferralRecord.is_deleted.is_(False),
        )
        .order_by(ReferralRecord.updated_at.desc(), ReferralRecord.created_at.desc(), ReferralRecord.id.desc())
    )
    referrers: dict[int, str] = {}
    for referred_user_id, snapshot_name, user_name, user_email in result.all():
        label = (
            normalize_display_value(user_name)
            or normalize_display_value(snapshot_name)
            or normalize_display_value(user_email)
        )
        if label:
            referrers.setdefault(int(referred_user_id), label)
    return referrers


async def _load_id_attachment_asset_ids(
    *,
    db: AsyncSession,
    user_ids: set[int],
) -> dict[int, int]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(User.id, User.data).where(
            User.id.in_(sorted(user_ids)),
            User.is_deleted.is_(False),
        )
    )
    id_attachment_asset_ids: dict[int, int] = {}
    for user_id, user_data in result.all():
        asset_id = _extract_id_attachment_asset_id(user_data)
        if asset_id is not None:
            id_attachment_asset_ids[int(user_id)] = asset_id
    return id_attachment_asset_ids


async def _load_timesheet_aggregates(
    *,
    db: AsyncSession,
    talents: Sequence[TalentProfile],
) -> tuple[dict[int, Decimal], dict[int, date]]:
    talent_ids = {int(talent.id) for talent in talents}
    user_to_talent_id = {int(talent.user_id): int(talent.id) for talent in talents}
    if not talent_ids and not user_to_talent_id:
        return {}, {}
    result = await db.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.is_deleted.is_(False),
            or_(
                ProjectTimesheetRecord.talent_profile_id.in_(sorted(talent_ids)),
                ProjectTimesheetRecord.user_id.in_(sorted(user_to_talent_id)),
            ),
        )
    )
    total_hours_by_talent: dict[int, Decimal] = {}
    recent_work_date_by_talent: dict[int, date] = {}
    for record in result.scalars().all():
        talent_id = int(record.talent_profile_id or user_to_talent_id.get(int(record.user_id), 0))
        if talent_id <= 0:
            continue
        if record.candidate_duration_hours is not None:
            total_hours_by_talent[talent_id] = (
                total_hours_by_talent.get(talent_id, Decimal("0")) + record.candidate_duration_hours
            )
        current_recent = recent_work_date_by_talent.get(talent_id)
        if current_recent is None or record.work_date > current_recent:
            recent_work_date_by_talent[talent_id] = record.work_date
    return total_hours_by_talent, recent_work_date_by_talent


async def load_talent_pool_sources(
    *,
    db: AsyncSession,
    talents: Sequence[TalentProfile],
) -> TalentPoolSourceBundle:
    talent_ids = {int(talent.id) for talent in talents}
    user_ids = {int(talent.user_id) for talent in talents}

    application_fields_by_user = await _load_application_extra_fields(db=db, user_ids=user_ids)
    referrer_name_by_user = await _load_referrers(db=db, user_ids=user_ids)
    id_attachment_asset_id_by_user = await _load_id_attachment_asset_ids(db=db, user_ids=user_ids)
    total_hours_by_talent, recent_work_date_by_talent = await _load_timesheet_aggregates(db=db, talents=talents)

    progress_by_talent: dict[int, JobProgress] = {}
    if talent_ids:
        progress_result = await db.execute(
            select(JobProgress)
            .where(
                JobProgress.talent_profile_id.in_(sorted(talent_ids)),
                JobProgress.is_deleted.is_(False),
            )
            .order_by(JobProgress.id.desc())
        )
        progress_by_talent = _choose_progress_by_talent(list(progress_result.scalars().all()))

    contract_by_talent: dict[int, ContractRecord] = {}
    if talent_ids:
        contract_result = await db.execute(
            select(ContractRecord)
            .where(
                ContractRecord.talent_profile_id.in_(sorted(talent_ids)),
                ContractRecord.is_deleted.is_(False),
                ContractRecord.is_current.is_(True),
            )
            .order_by(ContractRecord.id.desc())
        )
        contract_by_talent = _choose_contract_by_talent(list(contract_result.scalars().all()))

    asset_ids: set[int] = set()
    for talent in talents:
        if talent.resume_asset_id is not None:
            asset_ids.add(int(talent.resume_asset_id))
    for contract in contract_by_talent.values():
        for asset_id in (
            contract.company_sealed_contract_asset_id,
            contract.contract_attachment_asset_id,
        ):
            if asset_id is not None:
                asset_ids.add(int(asset_id))
    asset_ids.update(id_attachment_asset_id_by_user.values())

    asset_by_id: dict[int, Asset] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_by_id = {int(asset.id): asset for asset in asset_result.scalars().all()}

    return TalentPoolSourceBundle(
        application_fields_by_user=application_fields_by_user,
        progress_by_talent=progress_by_talent,
        contract_by_talent=contract_by_talent,
        referrer_name_by_user=referrer_name_by_user,
        total_hours_by_talent=total_hours_by_talent,
        recent_work_date_by_talent=recent_work_date_by_talent,
        asset_by_id=asset_by_id,
        id_attachment_asset_id_by_user=id_attachment_asset_id_by_user,
    )


def derive_talent_status(*, talent: TalentProfile, progress: JobProgress | None) -> tuple[str, str, bool]:
    data = progress.data if progress is not None and isinstance(progress.data, dict) else {}
    onboarding_date = normalize_display_value(data.get(JobProgressDataKey.ONBOARDING_DATE.value))
    if progress is not None and progress.current_stage == RecruitmentStage.REJECTED.value:
        return TALENT_STATUS_REJECTED, TALENT_STATUS_LABELS[TALENT_STATUS_REJECTED], False
    if progress is not None and progress.current_stage == RecruitmentStage.REPLACED.value:
        return TALENT_STATUS_REPLACED, TALENT_STATUS_LABELS[TALENT_STATUS_REPLACED], bool(onboarding_date)
    if not onboarding_date:
        return TALENT_STATUS_RECRUITING, TALENT_STATUS_LABELS[TALENT_STATUS_RECRUITING], False
    override = normalize_display_value(talent.status_override)
    status = override if override in TALENT_STATUS_LABELS else TALENT_STATUS_ACTIVE
    return status, TALENT_STATUS_LABELS[status], True


def get_progress_onboarding_date(progress: JobProgress | None) -> str | None:
    if progress is None or not isinstance(progress.data, dict):
        return None
    return normalize_display_value(progress.data.get(JobProgressDataKey.ONBOARDING_DATE.value))


def validate_manual_talent_status(status: str, progress: JobProgress | None) -> str:
    if status not in {TALENT_STATUS_ACTIVE, TALENT_STATUS_REPLACED, TALENT_STATUS_ON_LEAVE}:
        raise ValueError("Unsupported talent status.")
    if not get_progress_onboarding_date(progress):
        raise ValueError("Manual talent status requires an onboarding date.")
    return status


def build_talent_pool_extra_fields(
    talent: TalentProfile,
    sources: TalentPoolSourceBundle,
) -> dict[str, Any]:
    progress = sources.progress_by_talent.get(int(talent.id))
    progress_data = progress.data if progress is not None and isinstance(progress.data, dict) else {}
    contract = sources.contract_by_talent.get(int(talent.id))
    application_fields = sources.application_fields_by_user.get(int(talent.user_id), {})
    talent_status, talent_status_label, talent_status_editable = derive_talent_status(talent=talent, progress=progress)

    progress_rate = parse_decimal(progress_data.get(JobProgressDataKey.ACCEPTED_RATE.value))
    contract_rate = contract.rate if contract is not None else None
    accepted_hourly_rate = contract_rate if contract_rate is not None else progress_rate
    contract_number = normalize_display_value(
        contract.agreement_ref_no if contract is not None else None
    ) or normalize_display_value(progress_data.get(JobProgressDataKey.CONTRACT_NUMBER.value))
    contract_effective_date = contract.effective_date if contract is not None else None
    contract_end_date = contract.end_date if contract is not None else None
    id_attachment_asset_id = sources.id_attachment_asset_id_by_user.get(int(talent.user_id))

    return {
        "resume_attachment_asset": serialize_talent_attachment(
            sources.asset_by_id.get(int(talent.resume_asset_id or 0))
        ),
        "english_proficiency": application_fields.get(CandidateFieldKey.ENGLISH_PROFICIENCY.value),
        "age_range": application_fields.get(CandidateFieldKey.AGE_RANGE.value),
        "referrer_name": sources.referrer_name_by_user.get(int(talent.user_id)),
        "progress_language": normalize_display_value(progress_data.get(JobProgressDataKey.JOB_LANGUAGES.value)),
        "talent_status": talent_status,
        "talent_status_label": talent_status_label,
        "talent_status_editable": talent_status_editable,
        "contract_type": normalize_display_value(contract.contract_type if contract is not None else None),
        "accepted_hourly_rate": accepted_hourly_rate,
        "contract_number": contract_number,
        "contract_effective_date": contract_effective_date,
        "contract_end_date": contract_end_date,
        "company_sealed_contract_asset": serialize_talent_attachment(
            sources.asset_by_id.get(
                int((contract.company_sealed_contract_asset_id if contract is not None else None) or 0)
            )
        ),
        "id_attachment_asset": serialize_talent_attachment(sources.asset_by_id.get(int(id_attachment_asset_id or 0))),
        "onboarding_status": normalize_display_value(progress_data.get(JobProgressDataKey.ONBOARDING_STATUS.value)),
        "onboarding_date": parse_iso_date(progress_data.get(JobProgressDataKey.ONBOARDING_DATE.value)),
        "note": normalize_display_value(progress_data.get(JobProgressDataKey.NOTE.value)) or talent.note,
        "total_work_hours": sources.total_hours_by_talent.get(int(talent.id)),
        "recent_work_date": sources.recent_work_date_by_talent.get(int(talent.id)),
    }
