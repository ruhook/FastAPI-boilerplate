import re
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from ..contract_record.model import ContractRecord
from ..job.model import Job
from ..talent_profile.model import TalentProfile
from ..user.model import User
from .model import ProjectTimesheetRecord
from .schema import (
    CandidateTimesheetEntryRead,
    CandidateTimesheetReferralRewardRead,
    ProjectTimesheetNoteAssetRead,
    ProjectTimesheetRecordRead,
)

TWO_DECIMALS = Decimal("0.01")


def _get_timesheet_worker_name(user: User, talent: TalentProfile | None) -> str:
    return str((talent.full_name if talent and talent.full_name else None) or user.name or user.email or "").strip()


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


def _serialize_note_assets(
    record: ProjectTimesheetRecord,
    asset_map: dict[int, dict[str, Any]],
) -> list[ProjectTimesheetNoteAssetRead]:
    raw_ids = (record.data or {}).get("note_asset_ids")
    if not isinstance(raw_ids, list):
        return []
    items: list[ProjectTimesheetNoteAssetRead] = []
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
            )
        )
    return items


def _serialize_timesheet_record(
    record: ProjectTimesheetRecord,
    *,
    asset_map: dict[int, dict[str, Any]],
    team_leader_map: dict[int, dict[str, Any]] | None = None,
    admin_user_map: dict[int, dict[str, Any]] | None = None,
) -> ProjectTimesheetRecordRead:
    team_leader = team_leader_map.get(int(record.team_leader_user_id or 0)) if team_leader_map else None
    registrar = admin_user_map.get(int(record.created_by_admin_user_id or 0)) if admin_user_map else None
    return ProjectTimesheetRecordRead(
        id=record.id,
        version=record.version,
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
    )


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
        "note_images": [item.name for item in note_images],
    }


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
) -> CandidateTimesheetEntryRead:
    return CandidateTimesheetEntryRead(
        id=record.id,
        contract_record_id=int(record.contract_record_id or 0),
        project_id=record.project_id,
        project_name=project_name,
        project_code=record.sub_project_name,
        work_date=record.work_date,
        hours=_quantize_hours(_to_decimal(record.candidate_duration_hours)) or _zero_decimal(),
    )


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except Exception:
        return None


def _serialize_candidate_referral_rewards(
    contract_record: ContractRecord,
) -> list[CandidateTimesheetReferralRewardRead]:
    raw_rewards = (contract_record.data or {}).get("referral_rewards")
    if not isinstance(raw_rewards, list):
        return []
    rewards: list[CandidateTimesheetReferralRewardRead] = []
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
            )
        )
    return rewards
