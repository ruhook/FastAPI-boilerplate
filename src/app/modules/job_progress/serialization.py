from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..contract_record.model import ContractRecord
from ..job.const import JOB_DATA_SHOW_COMPENSATION_KEY
from ..job.model import Job
from ..user.model import User
from .candidate_presentation import (
    CandidatePresentation,
    build_candidate_presentation,
)
from .const import (
    JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP,
    JobProgressDataKey,
    RecruitmentStage,
    get_recruitment_stage_cn_name,
)
from .language_rules import (
    DEFAULT_PROGRESS_LANGUAGE,
    normalize_progress_language_value,
)
from .model import JobProgress
from .normalization import _ensure_utc_datetime, _normalize_text
from .schema import (
    ContractRecordDataRead,
    JobProgressContractAssetRead,
    JobProgressRead,
)
from .state import _has_assessment_invitation

CONTRACT_PROCESS_DATA_KEYS = {
    JobProgressDataKey.ACCEPTED_RATE.value,
    JobProgressDataKey.SIGNING_STATUS.value,
    JobProgressDataKey.CONTRACT_NUMBER.value,
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT.value,
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_AT.value,
    JobProgressDataKey.CONTRACT_REVIEW.value,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT.value,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID.value,
}

CONTRACT_PROCESS_ASSET_KEYS = {
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT,
}

CANDIDATE_VISIBLE_STAGE_LABELS = {
    "review": "Review",
    "assessment_file": "Assessment File",
    RecruitmentStage.ASSESSMENT_REVIEW.value: "Assessment Review",
    RecruitmentStage.SCREENING_PASSED.value: "Screening Passed",
    RecruitmentStage.CONTRACT_POOL.value: "Contract Pool",
    RecruitmentStage.ACTIVE.value: "Active",
    RecruitmentStage.REJECTED.value: "Rejected",
    RecruitmentStage.REPLACED.value: "Replaced",
}


def _get_candidate_visible_stage(progress: JobProgress, job: Job) -> str:
    if progress.current_stage == RecruitmentStage.PENDING_SCREENING.value:
        if job.assessment_enabled and _has_assessment_invitation(progress):
            return "assessment_file"
        return "review"
    return progress.current_stage


def _get_candidate_visible_stage_label(progress: JobProgress, visible_stage: str) -> str:
    if visible_stage == RecruitmentStage.ACTIVE.value and (progress.data or {}).get(
        JobProgressDataKey.ONBOARDING_DATE.value
    ):
        return "Successfully Onboarded"
    return CANDIDATE_VISIBLE_STAGE_LABELS.get(visible_stage, visible_stage)


def serialize_job_progress(progress: JobProgress) -> dict[str, Any]:
    return JobProgressRead(
        id=progress.id,
        job_id=progress.job_id,
        user_id=progress.user_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        current_stage=progress.current_stage,
        version=progress.version,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        screening_mode=progress.screening_mode,
        entered_stage_at=_ensure_utc_datetime(progress.entered_stage_at),
        created_at=_ensure_utc_datetime(progress.created_at),
        updated_at=_ensure_utc_datetime(progress.updated_at),
        data=_serialize_process_data(progress.data or {}, {}, exclude_contract_fields=True),
        process_assets={},
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=None,
            asset_map={},
        ),
    ).model_dump()


def _build_candidate_compensation_label(job: Job) -> str:
    if job.compensation_min is None and job.compensation_max is None:
        return "-"
    min_value = float(job.compensation_min or 0)
    max_value = float(job.compensation_max or job.compensation_min or 0)
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".")
    max_text = f"{max_value:.2f}".rstrip("0").rstrip(".")
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


def _should_show_candidate_compensation(job: Job) -> bool:
    return bool((job.data or {}).get(JOB_DATA_SHOW_COMPENSATION_KEY, True))


def _serialize_application_snapshot(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        snapshot[key] = row.display_value if row.display_value is not None else row.raw_value
    return snapshot


def _serialize_progress_process_data(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    payload = _serialize_process_data(
        progress_data,
        asset_map,
        exclude_contract_fields=True,
    )
    payload[JobProgressDataKey.JOB_LANGUAGES.value] = normalize_progress_language_value(
        payload.get(JobProgressDataKey.JOB_LANGUAGES.value, DEFAULT_PROGRESS_LANGUAGE)
    )
    return payload


def _serialize_application_assets(
    field_rows: list[CandidateApplicationFieldValue],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for row in field_rows:
        if row.asset_id is None:
            continue
        key = row.catalog_key or row.field_key
        asset_payload = asset_map.get(int(row.asset_id))
        if asset_payload is None:
            continue
        payload[key] = {
            "asset_id": int(row.asset_id),
            "name": row.display_value or row.raw_value or asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    return payload


def _extract_process_asset_ids(progress_data: dict[str, Any]) -> list[int]:
    asset_ids: list[int] = []
    for asset_id_key in JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP.values():
        value = progress_data.get(asset_id_key.value)
        if value is None or value == "":
            continue
        try:
            asset_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    for item in _get_assessment_submission_records(progress_data):
        value = item.get("asset_id")
        if value is None or value == "":
            continue
        try:
            asset_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return asset_ids


def _get_assessment_submission_records(progress_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    if isinstance(raw_items, list):
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        if items:
            return items

    legacy_asset_id = progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value)
    legacy_name = progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT.value)
    legacy_submitted_at = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value)
    if legacy_asset_id or legacy_name:
        return [
            {
                "asset_id": legacy_asset_id,
                "name": legacy_name,
                "submitted_at": legacy_submitted_at,
            }
        ]
    return []


def _serialize_assessment_submission_records(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in _get_assessment_submission_records(progress_data):
        asset_id_value = item.get("asset_id")
        asset_id: int | None = None
        if asset_id_value is not None and asset_id_value != "":
            try:
                asset_id = int(asset_id_value)
            except (TypeError, ValueError):
                asset_id = None

        asset_payload = asset_map.get(asset_id) if asset_id is not None else None
        payload.append(
            {
                "asset_id": asset_id,
                "name": item.get("name") or (asset_payload or {}).get("original_name") or "",
                "submitted_at": item.get("submitted_at") or "",
                "preview_url": asset_payload.get("preview_url") if asset_payload else None,
                "download_url": asset_payload.get("download_url") if asset_payload else None,
                "mime_type": asset_payload.get("mime_type") if asset_payload else None,
            }
        )
    return payload


def _serialize_process_data(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
    *,
    exclude_contract_fields: bool = False,
) -> dict[str, Any]:
    payload = dict(progress_data)
    if exclude_contract_fields:
        for key in CONTRACT_PROCESS_DATA_KEYS:
            payload.pop(key, None)
    assessment_submissions = _serialize_assessment_submission_records(progress_data, asset_map)
    if assessment_submissions:
        payload[JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value] = assessment_submissions
    return payload


def _serialize_process_assets(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
    *,
    exclude_contract_assets: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for file_name_key, asset_id_key in JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP.items():
        if exclude_contract_assets and file_name_key in CONTRACT_PROCESS_ASSET_KEYS:
            continue
        asset_id_value = progress_data.get(asset_id_key.value)
        if asset_id_value is None or asset_id_value == "":
            continue
        try:
            asset_id = int(asset_id_value)
        except (TypeError, ValueError):
            continue
        asset_payload = asset_map.get(asset_id)
        if asset_payload is None:
            continue
        payload[file_name_key.value] = {
            "asset_id": asset_id,
            "name": progress_data.get(file_name_key.value) or asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    return payload


def _extract_id_attachment_asset_id(user_data: dict[str, Any] | None) -> int | None:
    payment_info = (user_data or {}).get("payment_info")
    if not isinstance(payment_info, dict):
        return None
    raw_asset_id = payment_info.get("id_attachment_asset_id")
    if raw_asset_id in (None, "", 0):
        return None
    try:
        return int(raw_asset_id)
    except (TypeError, ValueError):
        return None


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


def _serialize_identity_attachment_asset(
    *,
    user_id: int,
    id_attachment_asset_ids_by_user: dict[int, int],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    asset_id = id_attachment_asset_ids_by_user.get(int(user_id))
    if asset_id is None:
        return {}
    asset_payload = asset_map.get(int(asset_id))
    if asset_payload is None:
        return {}
    return {
        "id_attachment": {
            "asset_id": int(asset_id),
            "name": asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    }


def _extract_contract_record_asset_ids(contract_record: ContractRecord | None) -> list[int]:
    if contract_record is None:
        return []
    asset_ids = [
        contract_record.draft_contract_asset_id,
        contract_record.candidate_signed_contract_asset_id,
        contract_record.company_sealed_contract_asset_id,
        contract_record.contract_attachment_asset_id,
    ]
    return [int(asset_id) for asset_id in asset_ids if asset_id not in (None, "")]


def _build_contract_asset_read(
    *,
    asset_id: int | None,
    display_name: str | None,
    asset_map: dict[int, dict[str, Any]],
) -> JobProgressContractAssetRead | None:
    if asset_id is None:
        return None
    asset_payload = asset_map.get(asset_id)
    if asset_payload is None:
        return None
    return JobProgressContractAssetRead(
        asset_id=asset_id,
        name=display_name or asset_payload.get("original_name") or "",
        preview_url=asset_payload.get("preview_url"),
        download_url=asset_payload.get("download_url"),
        mime_type=asset_payload.get("mime_type"),
    )


def _serialize_contract_record_data(
    *,
    progress: JobProgress,
    contract_record: ContractRecord | None,
    asset_map: dict[int, dict[str, Any]],
    current_company_name: str | None = None,
    current_project_name: str | None = None,
) -> ContractRecordDataRead | None:
    contract_data = (contract_record.data or {}) if contract_record is not None else {}

    if contract_record is None:
        return None

    draft_asset_id = contract_record.draft_contract_asset_id if contract_record is not None else None

    candidate_signed_asset_id = (
        contract_record.candidate_signed_contract_asset_id if contract_record is not None else None
    )

    company_sealed_asset_id = contract_record.company_sealed_contract_asset_id if contract_record is not None else None

    contract_attachment_asset_id = contract_record.contract_attachment_asset_id if contract_record is not None else None

    if contract_record.rate is not None:
        rate = format(contract_record.rate, "f").rstrip("0").rstrip(".")
    else:
        rate = None
    if getattr(contract_record, "base_pay", None) is not None:
        base_pay = format(contract_record.base_pay, "f")
    else:
        base_pay = None

    return ContractRecordDataRead(
        id=contract_record.id,
        user_id=contract_record.user_id,
        talent_profile_id=contract_record.talent_profile_id,
        application_id=contract_record.application_id,
        job_id=contract_record.job_id,
        job_progress_id=contract_record.job_progress_id,
        service_customer_company_id=contract_record.service_customer_company_id,
        service_customer_company_name=current_company_name,
        service_customer_project_id=contract_record.service_customer_project_id,
        service_customer_project_name=current_project_name,
        agreement_ref_no=contract_record.agreement_ref_no,
        contract_status=contract_record.contract_status,
        contract_type=contract_record.contract_type,
        contractor_name=contract_record.contractor_name,
        rate=rate,
        base_pay=base_pay,
        legal_entity=contract_record.legal_entity,
        worker_type=contract_record.worker_type,
        effective_date=contract_record.effective_date,
        end_date=contract_record.end_date,
        draft_contract_attachment=_build_contract_asset_read(
            asset_id=draft_asset_id,
            display_name=(_normalize_text(contract_data.get("draft_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        candidate_signed_contract_attachment=_build_contract_asset_read(
            asset_id=candidate_signed_asset_id,
            display_name=(_normalize_text(contract_data.get("candidate_signed_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        company_sealed_contract_attachment=_build_contract_asset_read(
            asset_id=company_sealed_asset_id,
            display_name=(_normalize_text(contract_data.get("company_sealed_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        contract_attachment=_build_contract_asset_read(
            asset_id=contract_attachment_asset_id,
            display_name=(
                _normalize_text(contract_data.get("contract_attachment_name"))
                or _normalize_text(contract_data.get("company_sealed_contract_attachment_name"))
                or _normalize_text(contract_data.get("candidate_signed_contract_attachment_name"))
                or None
            ),
            asset_map=asset_map,
        ),
        submitted_contract_at=(_normalize_text(contract_data.get("candidate_signed_contract_submitted_at")) or None),
        signing_status=_normalize_text(contract_data.get("signing_status")) or None,
        contract_review=_normalize_text(contract_data.get("contract_review")) or None,
        parse_status=contract_record.parse_status,
        parse_error=contract_record.parse_error,
        data=dict(contract_data),
    )


def _build_candidate_presentation_for_progress(
    *,
    progress: JobProgress,
    job: Job,
    contract_record: ContractRecord | None,
) -> CandidatePresentation:
    contract_data = (contract_record.data or {}) if contract_record is not None else {}
    presentation_contract_data = (
        {
            "draft_contract_attachment": contract_record.draft_contract_asset_id,
            "candidate_signed_contract_attachment": contract_record.candidate_signed_contract_asset_id,
            "company_sealed_contract_attachment": contract_record.company_sealed_contract_asset_id,
            "contract_attachment": contract_record.contract_attachment_asset_id,
            "submitted_contract_at": contract_data.get("candidate_signed_contract_submitted_at"),
            "contract_review": contract_data.get("contract_review"),
        }
        if contract_record is not None
        else None
    )
    return build_candidate_presentation(
        current_stage=progress.current_stage,
        assessment_enabled=job.assessment_enabled,
        process_data=progress.data or {},
        contract_data=presentation_contract_data,
    )
