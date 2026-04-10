import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..job.const import JOB_DATA_AUTOMATION_RULES_KEY
from ..job.model import Job
from ..admin.admin_user.model import AdminUser
from ..user.model import User
from ..admin.internal_notification.service import create_admin_internal_notification
from ..admin.mail_task.schema import MailRecipient, MailTaskCreate
from ..admin.mail_task.service import create_mail_task
from ..admin.mail_template.service import get_mail_template_model
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from .const import (
    JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP,
    JobProgressDataKey,
    RecruitmentScreeningMode,
    RecruitmentStage,
    get_allowed_recruitment_stage_transitions,
    get_recruitment_stage_cn_name,
)
from .model import JobProgress
from .schema import (
    CandidateJobApplicationDetailRead,
    CandidateJobApplicationListItemRead,
    CandidateJobApplicationListPage,
    JobProgressAssessmentUploadResponse,
    JobProgressCandidateSignedContractUploadResponse,
    JobProgressCompanySealedContractUploadResponse,
    JobProgressContractDraftUploadResponse,
    JobProgressListItemRead,
    JobProgressListPage,
    JobProgressRead,
)

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _build_field_value_map(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, dict[str, Any]]:
    value_map: dict[str, dict[str, Any]] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        value_map[key] = {
            "raw_value": row.raw_value,
            "display_value": row.display_value,
            "asset_id": row.asset_id,
        }
    return value_map


def _evaluate_automation_rule(
    rule: dict[str, Any],
    field_values: dict[str, dict[str, Any]],
) -> bool:
    field_key = _normalize_text(rule.get("fieldKey"))
    operator = _normalize_text(rule.get("operator")).lower()
    configured_value = rule.get("value")
    field_entry = field_values.get(field_key, {})
    display_value = field_entry.get("display_value")
    raw_value = field_entry.get("raw_value")
    asset_id = field_entry.get("asset_id")
    actual_value = display_value if display_value is not None else raw_value

    if operator == "uploaded":
        return asset_id is not None or _normalize_text(actual_value) != ""
    if operator == "not_uploaded":
        return asset_id is None and _normalize_text(actual_value) == ""
    if operator == "true":
        return _normalize_text(actual_value).lower() in {"true", "1", "yes"}
    if operator == "false":
        return _normalize_text(actual_value).lower() in {"false", "0", "no"}

    if operator in {"gt", "lt", "eq", "between"}:
        left = _normalize_number(actual_value)
        if left is None:
            return False
        if operator == "gt":
            right = _normalize_number(configured_value)
            return right is not None and left > right
        if operator == "lt":
            right = _normalize_number(configured_value)
            return right is not None and left < right
        if operator == "eq":
            right = _normalize_number(configured_value)
            if right is not None:
                return left == right
            return _normalize_text(actual_value).lower() == _normalize_text(configured_value).lower()
        if operator == "between":
            lower = _normalize_number(configured_value)
            upper = _normalize_number(rule.get("secondValue"))
            return lower is not None and upper is not None and lower <= left <= upper

    actual_text = _normalize_text(actual_value).lower()
    if operator == "contains":
        return _normalize_text(configured_value).lower() in actual_text
    if operator == "not_contains":
        return _normalize_text(configured_value).lower() not in actual_text
    if operator == "includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        normalized_actual_parts = {
            value.strip().lower()
            for value in actual_text.replace("/", ",").split(",")
            if value.strip()
        }
        return any(_normalize_text(item).lower() in normalized_actual_parts for item in target_values)
    if operator == "not_includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        normalized_actual_parts = {
            value.strip().lower()
            for value in actual_text.replace("/", ",").split(",")
            if value.strip()
        }
        return all(_normalize_text(item).lower() not in normalized_actual_parts for item in target_values)
    if operator == "eq":
        return actual_text == _normalize_text(configured_value).lower()

    return False


def _evaluate_automation_rules(
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[bool, bool]:
    data = job.data or {}
    rule_group = data.get(JOB_DATA_AUTOMATION_RULES_KEY) or {}
    rules = list(rule_group.get("rules") or [])
    if not rules:
        return False, False

    combinator = _normalize_text(rule_group.get("combinator") or "and").lower()
    field_values = _build_field_value_map(field_rows)
    results = [_evaluate_automation_rule(rule, field_values) for rule in rules if isinstance(rule, dict)]
    if not results:
        return False, False
    matched = all(results) if combinator != "or" else any(results)
    return True, matched


def _resolve_initial_stage(
    *,
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[RecruitmentStage, RecruitmentScreeningMode, str]:
    auto_screening_enabled, matched = _evaluate_automation_rules(job, field_rows)
    if not auto_screening_enabled:
        return (
            RecruitmentStage.PENDING_SCREENING,
            RecruitmentScreeningMode.MANUAL,
            "岗位未配置自动筛选规则，申请停留在待筛选名单。",
        )

    if matched and job.assessment_enabled:
        return (
            RecruitmentStage.ASSESSMENT_REVIEW,
            RecruitmentScreeningMode.AUTO,
            "自动筛选通过，且岗位开启测试题环节，进入测试题回收。",
        )
    if matched:
        return (
            RecruitmentStage.SCREENING_PASSED,
            RecruitmentScreeningMode.AUTO,
            "自动筛选通过，且岗位无需测试题，进入筛选通过。",
        )
    return (
        RecruitmentStage.REJECTED,
        RecruitmentScreeningMode.AUTO,
        "自动筛选未通过，进入淘汰。",
    )


def _get_job_mail_context(job: Job) -> dict[str, Any]:
    job_data = job.data or {}
    return {
        "job": {
            "title": job.title,
            "job_title": job.title,
            "assessment_link": str(job_data.get("assessment_link") or job_data.get("assessmentLink") or ""),
            "due_date": str(job_data.get("due_date") or job_data.get("dueDate") or ""),
        },
        "company": {
            "name": job.company_name,
            "company_name": job.company_name,
        },
    }


def _get_stage_mail_config(job: Job, target_stage: RecruitmentStage) -> dict[str, int] | None:
    if target_stage == RecruitmentStage.ASSESSMENT_REVIEW:
        if not (
            job.assessment_enabled
            and job.assessment_mail_account_id is not None
            and job.assessment_mail_template_id is not None
            and job.assessment_mail_signature_id is not None
        ):
            return None
        return {
            "account_id": int(job.assessment_mail_account_id),
            "template_id": int(job.assessment_mail_template_id),
            "signature_id": int(job.assessment_mail_signature_id),
        }

    if target_stage == RecruitmentStage.REJECTED:
        raw_config = (job.data or {}).get("rejection_mail_config") or {}
        if not isinstance(raw_config, dict) or not raw_config.get("enabled"):
            return None

        account_id = raw_config.get("mail_account_id")
        template_id = raw_config.get("mail_template_id")
        signature_id = raw_config.get("mail_signature_id")
        if account_id is None or template_id is None or signature_id is None:
            return None

        return {
            "account_id": int(account_id),
            "template_id": int(template_id),
            "signature_id": int(signature_id),
        }

    return None


async def _trigger_stage_mail_task(
    *,
    job: Job,
    application: CandidateApplication,
    target_stage: RecruitmentStage,
    db: AsyncSession,
) -> None:
    mail_config = _get_stage_mail_config(job, target_stage)
    if mail_config is None:
        return

    user_result = await db.execute(
        select(User).where(
            User.id == application.user_id,
            User.is_deleted.is_(False),
        )
    )
    candidate = user_result.scalar_one_or_none()
    candidate_email = (candidate.email if candidate is not None else None) or ""
    candidate_email = candidate_email.strip()
    if not candidate_email:
        logger.warning(
            "Skip auto mail because candidate email is empty",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )
        return

    candidate_name = (
        (candidate.name if candidate is not None else None)
        or candidate_email
    )

    try:
        template = await get_mail_template_model(
            mail_config["template_id"],
            db,
            admin_user_id=job.owner_admin_user_id,
        )
        render_context = _get_job_mail_context(job)
        render_context["candidate"] = {
            "name": candidate_name,
            "candidate_name": candidate_name,
            "email": candidate_email,
            "candidate_email": candidate_email,
        }
        await create_mail_task(
            MailTaskCreate(
                account_id=mail_config["account_id"],
                template_id=mail_config["template_id"],
                signature_id=mail_config["signature_id"],
                subject=template.subject_template,
                body_html=template.body_html,
                to_recipients=[MailRecipient(name=candidate_name, email=candidate_email)],
                render_context=render_context,
            ),
            db,
            admin_user_id=job.owner_admin_user_id,
        )
    except Exception:
        logger.exception(
            "Failed to create automatic stage mail task",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )


async def create_job_progress_for_application(
    *,
    job: Job,
    application: CandidateApplication,
    talent_profile_id: int | None,
    field_rows: list[CandidateApplicationFieldValue],
    db: AsyncSession,
) -> JobProgress:
    final_stage, screening_mode, reason = _resolve_initial_stage(job=job, field_rows=field_rows)

    progress = JobProgress(
        job_id=job.id,
        user_id=application.user_id,
        application_id=application.id,
        talent_profile_id=talent_profile_id,
        current_stage=final_stage.value,
        screening_mode=screening_mode.value,
        entered_stage_at=application.submitted_at,
        data={},
    )
    db.add(progress)
    await db.flush()

    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=job.id,
        application_id=application.id,
        talent_profile_id=talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CREATED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": job.id,
            "job_title": job.title,
            "current_stage": RecruitmentStage.PENDING_SCREENING.value,
            "current_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.PENDING_SCREENING.value),
            "screening_mode": screening_mode.value,
        },
    )

    if final_stage != RecruitmentStage.PENDING_SCREENING:
        await create_operation_log(
            db=db,
            user_id=application.user_id,
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": RecruitmentStage.PENDING_SCREENING.value,
                "from_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.PENDING_SCREENING.value),
                "to_stage": final_stage.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(final_stage.value),
                "reason": reason,
                "screening_mode": screening_mode.value,
            },
        )

    if final_stage in {RecruitmentStage.ASSESSMENT_REVIEW, RecruitmentStage.REJECTED}:
        await _trigger_stage_mail_task(
            job=job,
            application=application,
            target_stage=final_stage,
            db=db,
        )

    return progress


async def get_job_progress_by_application_id(
    *,
    application_id: int,
    db: AsyncSession,
) -> JobProgress | None:
    result = await db.execute(
        select(JobProgress).where(
            JobProgress.application_id == application_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def get_job_progress_models(
    *,
    job_id: int,
    progress_ids: list[int],
    db: AsyncSession,
) -> list[JobProgress]:
    result = await db.execute(
        select(JobProgress).where(
            JobProgress.job_id == job_id,
            JobProgress.id.in_(progress_ids),
            JobProgress.is_deleted.is_(False),
        )
    )
    items = result.scalars().all()
    if len(items) != len(set(progress_ids)):
        raise NotFoundException("Job progress record not found.")
    return items


def serialize_job_progress(progress: JobProgress) -> dict[str, Any]:
    return JobProgressRead(
        id=progress.id,
        job_id=progress.job_id,
        user_id=progress.user_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        screening_mode=progress.screening_mode,
        entered_stage_at=progress.entered_stage_at,
        created_at=progress.created_at,
        updated_at=progress.updated_at,
        data=_serialize_process_data(progress.data or {}, {}),
        process_assets={},
    ).model_dump()


def _build_candidate_compensation_label(job: Job) -> str:
    if job.compensation_min is None and job.compensation_max is None:
        return "-"
    min_value = float(job.compensation_min or 0)
    max_value = float(job.compensation_max or job.compensation_min or 0)
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".")
    max_text = f"{max_value:.2f}".rstrip("0").rstrip(".")
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


def _serialize_application_snapshot(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        snapshot[key] = row.display_value if row.display_value is not None else row.raw_value
    return snapshot


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
) -> dict[str, Any]:
    payload = dict(progress_data)
    assessment_submissions = _serialize_assessment_submission_records(progress_data, asset_map)
    if assessment_submissions:
        payload[JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value] = assessment_submissions
    return payload


def _serialize_process_assets(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for file_name_key, asset_id_key in JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP.items():
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


async def list_job_progress(
    *,
    job_id: int,
    current_stages: list[str] | None = None,
    reviewer_admin_user_id: int | None = None,
    db: AsyncSession,
) -> dict[str, Any]:
    normalized_stages = [stage for stage in (current_stages or []) if stage]
    result = await db.execute(
        select(JobProgress, CandidateApplication)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
            *( [JobProgress.current_stage.in_(normalized_stages)] if normalized_stages else [] ),
            *(
                [JobProgress.assessment_reviewer_admin_user_id == reviewer_admin_user_id]
                if reviewer_admin_user_id is not None
                else []
            ),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
    )
    rows = result.all()
    if not rows:
        return JobProgressListPage(items=[], total=0).model_dump()

    application_ids = [application.id for _, application in rows]
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id.in_(application_ids))
        .order_by(
            CandidateApplicationFieldValue.application_id.asc(),
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = field_result.scalars().all()
    grouped_field_rows: dict[int, list[CandidateApplicationFieldValue]] = defaultdict(list)
    for row in field_rows:
        grouped_field_rows[int(row.application_id)].append(row)

    asset_ids = {int(row.asset_id) for row in field_rows if row.asset_id is not None}
    for progress, _ in rows:
        asset_ids.update(_extract_process_asset_ids(progress.data or {}))
    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    items = [
        JobProgressListItemRead(
            id=progress.id,
            job_id=progress.job_id,
            user_id=progress.user_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            current_stage=progress.current_stage,
            current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
            screening_mode=progress.screening_mode,
            applied_at=application.submitted_at,
            job_title=application.job_snapshot_title,
            job_company_name=application.job_snapshot_company_name,
            application_snapshot=_serialize_application_snapshot(grouped_field_rows.get(application.id, [])),
            application_assets=_serialize_application_assets(grouped_field_rows.get(application.id, []), asset_map),
            process_data=_serialize_process_data(progress.data or {}, asset_map),
            process_assets=_serialize_process_assets(progress.data or {}, asset_map),
        )
        for progress, application in rows
    ]
    return JobProgressListPage(items=items, total=len(items)).model_dump()


async def list_candidate_job_applications(
    *,
    user_id: int,
    page: int,
    page_size: int,
    keyword: str | None = None,
    current_stage: str | None = None,
    needs_action_only: bool = False,
    db: AsyncSession,
) -> dict[str, Any]:
    conditions = [
        JobProgress.user_id == user_id,
        JobProgress.is_deleted.is_(False),
        CandidateApplication.is_deleted.is_(False),
        Job.is_deleted.is_(False),
    ]
    normalized_keyword = _normalize_text(keyword)
    if normalized_keyword:
        term = f"%{normalized_keyword}%"
        conditions.append(
            or_(
                CandidateApplication.job_snapshot_title.ilike(term),
                CandidateApplication.job_snapshot_company_name.ilike(term),
            )
        )
    normalized_stage = _normalize_text(current_stage)
    if normalized_stage:
        conditions.append(JobProgress.current_stage == normalized_stage)
    if needs_action_only:
        conditions.append(
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.ASSESSMENT_REVIEW.value,
                    RecruitmentStage.CONTRACT_POOL.value,
                ]
            )
        )

    total_result = await db.execute(
        select(func.count())
        .select_from(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)
    if total == 0:
        return CandidateJobApplicationListPage(items=[], total=0, page=page, page_size=page_size).model_dump()

    result = await db.execute(
        select(JobProgress, CandidateApplication, Job)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .where(*conditions)
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()
    if not rows:
        return CandidateJobApplicationListPage(items=[], total=total, page=page, page_size=page_size).model_dump()

    application_ids = [application.id for _, application, _ in rows]
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id.in_(application_ids))
        .order_by(
            CandidateApplicationFieldValue.application_id.asc(),
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = field_result.scalars().all()
    grouped_field_rows: dict[int, list[CandidateApplicationFieldValue]] = defaultdict(list)
    for row in field_rows:
        grouped_field_rows[int(row.application_id)].append(row)

    asset_ids = {int(row.asset_id) for row in field_rows if row.asset_id is not None}
    for progress, _, _ in rows:
        asset_ids.update(_extract_process_asset_ids(progress.data or {}))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    items = [
        CandidateJobApplicationListItemRead(
            application_id=application.id,
            job_progress_id=progress.id,
            job_id=progress.job_id,
            job_title=application.job_snapshot_title,
            job_company_name=application.job_snapshot_company_name,
            job_status=job.status,
            current_stage=progress.current_stage,
            current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
            screening_mode=progress.screening_mode,
            applied_at=application.submitted_at,
            application_snapshot=_serialize_application_snapshot(grouped_field_rows.get(application.id, [])),
            application_assets=_serialize_application_assets(grouped_field_rows.get(application.id, []), asset_map),
            process_data=_serialize_process_data(progress.data or {}, asset_map),
            process_assets=_serialize_process_assets(progress.data or {}, asset_map),
        )
        for progress, application, job in rows
    ]
    return CandidateJobApplicationListPage(items=items, total=total, page=page, page_size=page_size).model_dump()


async def get_candidate_job_application_detail(
    *,
    user_id: int,
    application_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    result = await db.execute(
        select(JobProgress, CandidateApplication, Job)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .where(
            JobProgress.user_id == user_id,
            JobProgress.application_id == application_id,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
            Job.is_deleted.is_(False),
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Application not found.")

    progress, application, job = row
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id == application.id)
        .order_by(
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = list(field_result.scalars().all())

    asset_ids = {int(item.asset_id) for item in field_rows if item.asset_id is not None}
    asset_ids.update(_extract_process_asset_ids(progress.data or {}))
    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    return CandidateJobApplicationDetailRead(
        application_id=application.id,
        job_progress_id=progress.id,
        job_id=job.id,
        job_title=application.job_snapshot_title,
        job_company_name=application.job_snapshot_company_name,
        job_status=job.status,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        screening_mode=progress.screening_mode,
        applied_at=application.submitted_at,
        description_html=job.description,
        country=job.country,
        work_mode=job.work_mode,
        compensation_label=_build_candidate_compensation_label(job),
        assessment_enabled=job.assessment_enabled,
        application_snapshot=_serialize_application_snapshot(field_rows),
        application_assets=_serialize_application_assets(field_rows, asset_map),
        process_data=_serialize_process_data(progress.data or {}, asset_map),
        process_assets=_serialize_process_assets(progress.data or {}, asset_map),
    ).model_dump()


async def move_job_progress_stage(
    *,
    job_id: int,
    progress_ids: list[int],
    target_stage: str,
    admin_user_id: int,
    db: AsyncSession,
    reason: str | None = None,
    reviewer_scope_admin_user_id: int | None = None,
) -> dict[str, Any]:
    try:
        normalized_target_stage = RecruitmentStage(target_stage)
    except Exception as exc:
        raise BadRequestException("Unsupported target stage.") from exc

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    application_ids = [progress.application_id for progress in progress_items]
    application_map: dict[int, CandidateApplication] = {}
    if application_ids:
        application_result = await db.execute(
            select(CandidateApplication).where(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        application_map = {
            int(application.id): application
            for application in application_result.scalars().all()
        }

    for progress in progress_items:
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")
        allowed_targets = get_allowed_recruitment_stage_transitions(
            progress.current_stage,
            assessment_enabled=job.assessment_enabled,
        )
        if normalized_target_stage not in allowed_targets:
            raise BadRequestException(
                f"Current stage {progress.current_stage} cannot move to {normalized_target_stage.value}."
            )

    for progress in progress_items:
        from_stage = progress.current_stage
        next_data = dict(progress.data or {})
        if normalized_target_stage == RecruitmentStage.REJECTED:
            next_data[JobProgressDataKey.REJECTED_FROM_STAGE.value] = from_stage
        elif JobProgressDataKey.REJECTED_FROM_STAGE.value in next_data:
            next_data.pop(JobProgressDataKey.REJECTED_FROM_STAGE.value, None)

        progress.current_stage = normalized_target_stage.value
        progress.entered_stage_at = datetime.now(UTC)
        progress.data = next_data

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": from_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(from_stage),
                "to_stage": normalized_target_stage.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(normalized_target_stage.value),
                "operator_admin_user_id": admin_user_id,
                "reason": reason or "",
            },
        )

        if normalized_target_stage in {RecruitmentStage.ASSESSMENT_REVIEW, RecruitmentStage.REJECTED}:
            application = application_map.get(int(progress.application_id))
            if application is not None:
                await _trigger_stage_mail_task(
                    job=job,
                    application=application,
                    target_stage=normalized_target_stage,
                    db=db,
                )

    await db.flush()
    return {
        "updated_count": len(progress_items),
        "target_stage": normalized_target_stage.value,
        "target_stage_cn_name": get_recruitment_stage_cn_name(normalized_target_stage.value),
    }


async def execute_job_progress_assessment_automation(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    reviewer_scope_admin_user_id: int | None = None,
) -> dict[str, Any]:
    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)

    passed_ids: list[int] = []
    rejected_ids: list[int] = []
    untouched_ids: list[int] = []

    for progress in progress_items:
        if progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can execute automation.")
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")

        assessment_result = _normalize_text((progress.data or {}).get(JobProgressDataKey.ASSESSMENT_RESULT.value))
        if assessment_result in {"通过", "待定"}:
            passed_ids.append(progress.id)
        elif assessment_result == "不通过":
            rejected_ids.append(progress.id)
        else:
            untouched_ids.append(progress.id)

    if passed_ids:
        await move_job_progress_stage(
            job_id=job_id,
            progress_ids=passed_ids,
            target_stage=RecruitmentStage.SCREENING_PASSED.value,
            admin_user_id=admin_user_id,
            db=db,
            reason="assessment_automation_passed",
            reviewer_scope_admin_user_id=reviewer_scope_admin_user_id,
        )
    if rejected_ids:
        await move_job_progress_stage(
            job_id=job_id,
            progress_ids=rejected_ids,
            target_stage=RecruitmentStage.REJECTED.value,
            admin_user_id=admin_user_id,
            db=db,
            reason="assessment_automation_rejected",
            reviewer_scope_admin_user_id=reviewer_scope_admin_user_id,
        )

    return {
        "passed_count": len(passed_ids),
        "rejected_count": len(rejected_ids),
        "untouched_count": len(untouched_ids),
    }


async def update_job_progress_assessment_review(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    reviewer_scope_admin_user_id: int | None = None,
    assessment_result: str | None = None,
    assessment_review_comment: str | None = None,
    assessment_reviewer: str | None = None,
    assessment_reviewer_admin_user_id: int | None = None,
) -> dict[str, Any]:
    field_updates: dict[JobProgressDataKey, Any] = {}
    if assessment_result is not None:
        field_updates[JobProgressDataKey.ASSESSMENT_RESULT] = assessment_result
    if assessment_review_comment is not None:
        field_updates[JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT] = assessment_review_comment
    if assessment_reviewer is not None:
        field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER] = assessment_reviewer
    if assessment_reviewer_admin_user_id is not None:
        field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER_ADMIN_USER_ID] = assessment_reviewer_admin_user_id

    if not field_updates:
        raise BadRequestException("At least one assessment review field is required.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    candidate_users: dict[int, User] = {}
    if progress_items:
        user_result = await db.execute(
            select(User).where(
                User.id.in_([progress.user_id for progress in progress_items]),
                User.is_deleted.is_(False),
            )
        )
        candidate_users = {item.id: item for item in user_result.scalars().all()}
    sender_admin_name: str | None = None
    sender_result = await db.execute(
        select(AdminUser).where(
            AdminUser.id == admin_user_id,
            AdminUser.is_deleted.is_(False),
        )
    )
    sender_admin = sender_result.scalar_one_or_none()
    if sender_admin is not None:
        sender_admin_name = sender_admin.name

    for progress in progress_items:
        if progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can be updated here.")
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")

    updated_field_keys = [key.value for key in field_updates]
    for progress in progress_items:
        next_data = dict(progress.data or {})
        changed_fields: dict[str, dict[str, Any]] = {}
        for field_key, next_value in field_updates.items():
            previous_value = next_data.get(field_key.value)
            if previous_value == next_value:
                continue
            next_data[field_key.value] = next_value
            changed_fields[field_key.value] = {
                "from": previous_value,
                "to": next_value,
            }

        if not changed_fields:
            continue

        progress.data = next_data
        if JobProgressDataKey.ASSESSMENT_REVIEWER_ADMIN_USER_ID in field_updates:
            progress.assessment_reviewer_admin_user_id = assessment_reviewer_admin_user_id
            progress.assessment_assigned_at = datetime.now(UTC)

        if (
            "assessment_reviewer_admin_user_id" in changed_fields
            and assessment_reviewer_admin_user_id is not None
        ):
            candidate = candidate_users.get(progress.user_id)
            candidate_name = (
                (candidate.name if candidate is not None else None)
                or (candidate.email if candidate is not None else None)
                or f"候选人#{progress.user_id}"
            )
            await create_admin_internal_notification(
                db=db,
                recipient_admin_user_id=assessment_reviewer_admin_user_id,
                sender_admin_user_id=admin_user_id,
                category="assessment_assignment",
                title="收到新的测试题判题任务",
                description=f"已将 {candidate_name} 的测试题分配到您这边，请及时完成评审。",
                action_url=f"/jobs/{job.id}/progress?stage=assessment&candidateId={progress.user_id}",
                data={
                    "job_id": job.id,
                    "job_title": job.title,
                    "progress_id": progress.id,
                    "candidate_user_id": progress.user_id,
                    "application_id": progress.application_id,
                    "stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                    "sender_name": sender_admin_name,
                    "candidate_name": candidate_name,
                },
            )

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "updated_fields": changed_fields,
            },
        )

    await db.flush()
    return {
        "updated_count": len(progress_items),
        "updated_field_keys": updated_field_keys,
    }


async def submit_job_progress_assessment(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    if not job.assessment_enabled:
        raise BadRequestException("This job does not accept assessment uploads.")

    progress_result = await db.execute(
        select(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.user_id == user_id,
            JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Assessment upload record not found for this job.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="user",
            owner_id=user_id,
        ),
        upload=upload,
    )

    submitted_at = datetime.now(UTC)
    next_data = dict(progress.data or {})
    submission_records = _get_assessment_submission_records(next_data)
    submission_records.append(
        {
            "asset_id": int(asset_payload["id"]),
            "name": asset_payload["original_name"],
            "submitted_at": submitted_at.isoformat(),
        }
    )
    next_data[JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value] = submission_records
    next_data[JobProgressDataKey.ASSESSMENT_ATTACHMENT.value] = asset_payload["original_name"]
    next_data[JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value] = int(asset_payload["id"])
    next_data[JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value] = submitted_at.isoformat()
    progress.data = next_data

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "assessment_asset_id": int(asset_payload["id"]),
            "assessment_attachment": asset_payload["original_name"],
            "assessment_submitted_at": next_data[JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value],
            "assessment_submission_count": len(submission_records),
        },
    )
    await db.flush()

    serialized_asset = {
        "asset_id": int(asset_payload["id"]),
        "name": asset_payload["original_name"],
        "preview_url": asset_payload["preview_url"],
        "download_url": asset_payload["download_url"],
        "mime_type": asset_payload["mime_type"],
    }
    return JobProgressAssessmentUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        assessment_asset=asset_payload,
        process_data=_serialize_process_data(next_data, {int(asset_payload["id"]): asset_payload}),
        process_assets={JobProgressDataKey.ASSESSMENT_ATTACHMENT.value: serialized_asset},
    ).model_dump()


async def submit_job_progress_candidate_signed_contract(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.user_id == user_id,
            JobProgress.current_stage == RecruitmentStage.CONTRACT_POOL.value,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Signed contract upload record not found for this job.")

    progress_data = dict(progress.data or {})
    draft_contract_asset_id = progress_data.get(JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value)
    if draft_contract_asset_id in (None, "", 0, "0"):
        raise BadRequestException("Draft contract is not available yet.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="user",
            owner_id=user_id,
        ),
        upload=upload,
    )

    submitted_at = datetime.now(UTC)
    next_data = dict(progress_data)
    next_data[JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT.value] = asset_payload["original_name"]
    next_data[JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID.value] = int(asset_payload["id"])
    next_data[JobProgressDataKey.SUBMITTED_CONTRACT_AT.value] = submitted_at.isoformat()
    progress.data = next_data

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "submitted_contract_asset_id": int(asset_payload["id"]),
            "submitted_contract_attachment": asset_payload["original_name"],
            "submitted_contract_at": next_data[JobProgressDataKey.SUBMITTED_CONTRACT_AT.value],
        },
    )
    await db.flush()

    serialized_asset = {
        "asset_id": int(asset_payload["id"]),
        "name": asset_payload["original_name"],
        "preview_url": asset_payload["preview_url"],
        "download_url": asset_payload["download_url"],
        "mime_type": asset_payload["mime_type"],
    }
    return JobProgressCandidateSignedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        candidate_signed_contract_asset=asset_payload,
        process_data=next_data,
        process_assets={JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT.value: serialized_asset},
    ).model_dump()


async def upload_job_progress_contract_draft(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress).where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    }:
        raise BadRequestException("Contract draft can only be uploaded in 筛选通过 or 合同库.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )

    next_data = dict(progress.data or {})
    next_data[JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT.value] = asset_payload["original_name"]
    next_data[JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value] = int(asset_payload["id"])
    progress.data = next_data

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "contract_draft_asset_id": int(asset_payload["id"]),
            "contract_draft_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )
    await db.flush()

    serialized_asset = {
        "asset_id": int(asset_payload["id"]),
        "name": asset_payload["original_name"],
        "preview_url": asset_payload["preview_url"],
        "download_url": asset_payload["download_url"],
        "mime_type": asset_payload["mime_type"],
    }
    return JobProgressContractDraftUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        contract_draft_asset=asset_payload,
        process_data=next_data,
        process_assets={JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT.value: serialized_asset},
    ).model_dump()


async def upload_job_progress_company_sealed_contract(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress).where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage != RecruitmentStage.CONTRACT_POOL.value:
        raise BadRequestException("Company sealed contract can only be uploaded in 合同库.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )

    next_data = dict(progress.data or {})
    next_data[JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT.value] = asset_payload["original_name"]
    next_data[JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID.value] = int(asset_payload["id"])
    progress.data = next_data

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )
    await db.flush()

    serialized_asset = {
        "asset_id": int(asset_payload["id"]),
        "name": asset_payload["original_name"],
        "preview_url": asset_payload["preview_url"],
        "download_url": asset_payload["download_url"],
        "mime_type": asset_payload["mime_type"],
    }
    return JobProgressCompanySealedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        company_sealed_contract_asset=asset_payload,
        process_data=next_data,
        process_assets={JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT.value: serialized_asset},
    ).model_dump()
