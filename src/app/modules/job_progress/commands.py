from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_TERMINATED,
    ContractReviewStatus,
    ContractSigningStatus,
)
from ..contract_record.model import ContractRecord
from ..contract_record.queries import get_current_contract_record_by_progress_id
from ..contract_record.serialization import get_default_contract_end_date
from ..job.const import JOB_DATA_LANGUAGES_KEY
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..referral_bonus_model.service import ensure_user_referral_profile_from_job
from .automation import _field_row_value, _resolve_initial_stage
from .const import (
    JobProgressDataKey,
    RecruitmentStage,
    get_allowed_recruitment_stage_transitions,
    get_recruitment_stage_cn_name,
)
from .language_rules import normalize_progress_language_value, resolve_progress_language
from .mail_workflow import _trigger_stage_mail_task
from .model import JobProgress
from .normalization import _has_assessment_attachment, _normalize_text
from .rejection_restore import build_rejected_progress_data
from .schema import JobProgressOnboardingUpdateResponse
from .state import get_job_progress_models


async def create_job_progress_for_application(
    *,
    job: Job,
    application: CandidateApplication,
    talent_profile_id: int | None,
    field_rows: list[CandidateApplicationFieldValue],
    db: AsyncSession,
) -> JobProgress:
    final_stage, screening_mode, reason, should_send_assessment_invite = _resolve_initial_stage(
        job=job,
        field_rows=field_rows,
    )
    progress_language = resolve_progress_language(
        job_country=job.country,
        job_language_requirements=(job.data or {}).get(JOB_DATA_LANGUAGES_KEY),
        candidate_country_of_residence=_field_row_value(field_rows, "country_of_residence"),
        candidate_native_languages=_field_row_value(field_rows, "native_languages"),
    )

    progress = JobProgress(
        job_id=job.id,
        user_id=application.user_id,
        application_id=application.id,
        talent_profile_id=talent_profile_id,
        current_stage=final_stage.value,
        screening_mode=screening_mode.value,
        entered_stage_at=application.submitted_at,
        data={JobProgressDataKey.JOB_LANGUAGES.value: progress_language},
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

    if final_stage == RecruitmentStage.REJECTED:
        await _trigger_stage_mail_task(
            job=job,
            application=application,
            target_stage=final_stage,
            db=db,
        )
    elif should_send_assessment_invite and final_stage == RecruitmentStage.PENDING_SCREENING:
        await _trigger_stage_mail_task(
            job=job,
            application=application,
            target_stage=RecruitmentStage.ASSESSMENT_REVIEW,
            db=db,
            progress=progress,
        )

    return progress


async def move_job_progress_stage(  # noqa: C901
    *,
    job_id: int,
    progress_ids: list[int],
    target_stage: str,
    admin_user_id: int,
    db: AsyncSession,
    reason: str | None = None,
    reviewer_scope_admin_user_id: int | None = None,
    expected_versions: dict[int, int] | None = None,
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

    progress_items = await get_job_progress_models(
        job_id=job_id,
        progress_ids=progress_ids,
        db=db,
        expected_versions=expected_versions,
    )
    application_ids = [progress.application_id for progress in progress_items]
    application_map: dict[int, CandidateApplication] = {}
    if application_ids:
        application_result = await db.execute(
            select(CandidateApplication).where(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        application_map = {int(application.id): application for application in application_result.scalars().all()}

    active_contract_record_map: dict[int, ContractRecord] = {}
    leaving_active_contract_record_map: dict[int, ContractRecord] = {}

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
        if progress.current_stage == RecruitmentStage.REJECTED.value:
            rejected_from_stage = _normalize_text(
                (progress.data or {}).get(JobProgressDataKey.REJECTED_FROM_STAGE.value)
            )
            allowed_restore_stages = {
                RecruitmentStage.PENDING_SCREENING.value,
                RecruitmentStage.ASSESSMENT_REVIEW.value,
                RecruitmentStage.SCREENING_PASSED.value,
                RecruitmentStage.CONTRACT_POOL.value,
            }
            if rejected_from_stage not in allowed_restore_stages:
                raise BadRequestException("Rejected progress record is missing a supported source stage.")
            if normalized_target_stage.value != rejected_from_stage:
                raise BadRequestException("Rejected progress record can only restore to its source stage.")
        if (
            progress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value
            and normalized_target_stage == RecruitmentStage.SCREENING_PASSED
        ):
            if not _has_assessment_attachment(progress):
                raise BadRequestException("Screening passed stage requires an assessment submission.")
            assessment_result = _normalize_text((progress.data or {}).get(JobProgressDataKey.ASSESSMENT_RESULT.value))
            if assessment_result not in {"通过", "待定"}:
                raise BadRequestException("Screening passed stage requires assessment result 通过 or 待定.")
        if (
            progress.current_stage == RecruitmentStage.SCREENING_PASSED.value
            and normalized_target_stage == RecruitmentStage.ASSESSMENT_REVIEW
        ):
            qa_status = _normalize_text((progress.data or {}).get(JobProgressDataKey.QA_STATUS.value))
            if qa_status != "待返修":
                raise BadRequestException("Only QA rework records can move back to assessment review.")

        if normalized_target_stage == RecruitmentStage.ACTIVE:
            contract_record = await get_current_contract_record_by_progress_id(
                progress_id=progress.id, db=db, for_update=True
            )
            if contract_record is None:
                raise BadRequestException("Active stage requires a contract record.")
            if contract_record.signing_status != ContractSigningStatus.COMPANY_SEALED.value:
                raise BadRequestException("Active stage requires a company sealed contract.")
            if contract_record.contract_review_status != ContractReviewStatus.APPROVED.value:
                raise BadRequestException("Active stage requires an approved contract review.")
            active_contract_record_map[progress.id] = contract_record
        if (
            progress.current_stage == RecruitmentStage.CONTRACT_POOL.value
            and normalized_target_stage == RecruitmentStage.SCREENING_PASSED
        ):
            contract_record = await get_current_contract_record_by_progress_id(
                progress_id=progress.id, db=db, for_update=True
            )
            if contract_record is not None and (
                contract_record.company_sealed_contract_asset_id not in (None, 0, "")
                or contract_record.contract_status == CONTRACT_STATUS_ACTIVE
            ):
                raise BadRequestException("Signed active contracts cannot move back to screening passed.")
        if progress.current_stage == RecruitmentStage.ACTIVE.value and normalized_target_stage in {
            RecruitmentStage.REPLACED,
            RecruitmentStage.REJECTED,
        }:
            contract_record = await get_current_contract_record_by_progress_id(
                progress_id=progress.id, db=db, for_update=True
            )
            if contract_record is None:
                raise BadRequestException("Leaving active stage requires a contract record.")
            leaving_active_contract_record_map[progress.id] = contract_record

    for progress in progress_items:
        from_stage = progress.current_stage
        next_data = dict(progress.data or {})
        if normalized_target_stage == RecruitmentStage.REJECTED:
            next_data = build_rejected_progress_data(
                next_data,
                source_stage=from_stage,
            )
        elif JobProgressDataKey.REJECTED_FROM_STAGE.value in next_data:
            next_data.pop(JobProgressDataKey.REJECTED_FROM_STAGE.value, None)
        if normalized_target_stage == RecruitmentStage.SCREENING_PASSED:
            next_data.pop(JobProgressDataKey.QA_STATUS.value, None)
        if normalized_target_stage == RecruitmentStage.CONTRACT_POOL:
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "可发合同"
        if normalized_target_stage == RecruitmentStage.ACTIVE and from_stage != RecruitmentStage.REJECTED.value:
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"

        progress.current_stage = normalized_target_stage.value
        progress.entered_stage_at = datetime.now(UTC)
        progress.data = next_data

        if normalized_target_stage == RecruitmentStage.ACTIVE:
            contract_record = active_contract_record_map[progress.id]
            contract_record.contract_status = CONTRACT_STATUS_ACTIVE
            contract_record.updated_by_admin_user_id = admin_user_id
            await ensure_user_referral_profile_from_job(
                user_id=int(progress.user_id),
                job=job,
                db=db,
                admin_user_id=admin_user_id,
                contract_record=contract_record,
            )
        if progress.id in leaving_active_contract_record_map:
            contract_record = leaving_active_contract_record_map[progress.id]
            contract_record.contract_status = CONTRACT_STATUS_TERMINATED
            contract_record.end_date = contract_record.end_date or get_default_contract_end_date(
                contract_record.effective_date or datetime.now(UTC).date()
            )
            contract_record.updated_by_admin_user_id = admin_user_id

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

        should_trigger_stage_mail = (
            normalized_target_stage == RecruitmentStage.REJECTED and from_stage != RecruitmentStage.ACTIVE.value
        )
        if should_trigger_stage_mail:
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
    missing_attachment_ids: list[int] = []
    missing_result_ids: list[int] = []

    for progress in progress_items:
        if progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can execute automation.")
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")

        if not _has_assessment_attachment(progress):
            untouched_ids.append(progress.id)
            missing_attachment_ids.append(progress.id)
            continue

        assessment_result = _normalize_text((progress.data or {}).get(JobProgressDataKey.ASSESSMENT_RESULT.value))
        if assessment_result in {"通过", "待定"}:
            passed_ids.append(progress.id)
        elif assessment_result == "不通过":
            rejected_ids.append(progress.id)
        else:
            untouched_ids.append(progress.id)
            missing_result_ids.append(progress.id)

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
        "missing_attachment_count": len(missing_attachment_ids),
        "missing_result_count": len(missing_result_ids),
    }
async def update_job_progress_note(
    *,
    job_id: int,
    progress_ids: list[int],
    note: str | None,
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

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    normalized_note = (note or "").strip()
    changed_count = 0

    for progress in progress_items:
        next_data = dict(progress.data or {})
        previous_value = _normalize_text(next_data.get(JobProgressDataKey.NOTE.value))
        if previous_value == normalized_note:
            continue

        if normalized_note:
            next_data[JobProgressDataKey.NOTE.value] = normalized_note
        else:
            next_data.pop(JobProgressDataKey.NOTE.value, None)
        progress.data = next_data
        changed_count += 1

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_NOTE_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "updated_fields": {
                    JobProgressDataKey.NOTE.value: {
                        "from": previous_value,
                        "to": normalized_note,
                    },
                },
            },
        )

    await db.flush()
    return {
        "updated_count": changed_count,
        "updated_field_keys": [JobProgressDataKey.NOTE.value],
    }


async def update_job_progress_language(
    *,
    job_id: int,
    progress_ids: list[int],
    language: str,
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

    if not _normalize_text(language):
        raise BadRequestException("Language is required.")
    normalized_language = normalize_progress_language_value(language)

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    changed_count = 0
    for progress in progress_items:
        next_data = dict(progress.data or {})
        previous_value = normalize_progress_language_value(next_data.get(JobProgressDataKey.JOB_LANGUAGES.value))
        if previous_value == normalized_language:
            continue

        next_data[JobProgressDataKey.JOB_LANGUAGES.value] = normalized_language
        progress.data = next_data
        changed_count += 1

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_LANGUAGE_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "updated_fields": {
                    JobProgressDataKey.JOB_LANGUAGES.value: {
                        "from": previous_value,
                        "to": normalized_language,
                    },
                },
            },
        )

    await db.flush()
    return {
        "updated_count": changed_count,
        "updated_field_keys": [JobProgressDataKey.JOB_LANGUAGES.value],
    }


def _format_current_process_datetime() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_current_process_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def update_job_progress_onboarding(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    onboarding_status: str | None = None,
    onboarding_date: date | None = None,
    salary_confirmed_at: str | None = None,
    gift_package_sent_at: str | None = None,
    update_onboarding_status: bool = False,
    update_onboarding_date: bool = False,
    update_salary_confirmed_at: bool = False,
    update_gift_package_sent_at: bool = False,
) -> dict[str, Any]:
    has_onboarding_status_update = update_onboarding_status or onboarding_status is not None
    has_onboarding_date_update = update_onboarding_date or onboarding_date is not None
    has_salary_confirmed_at_update = update_salary_confirmed_at or salary_confirmed_at is not None
    has_gift_package_sent_at_update = update_gift_package_sent_at or gift_package_sent_at is not None
    if not (
        has_onboarding_status_update
        or has_onboarding_date_update
        or has_salary_confirmed_at_update
        or has_gift_package_sent_at_update
    ):
        raise BadRequestException("At least one onboarding field is required.")

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
    allowed_stages = {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
        RecruitmentStage.ACTIVE.value,
        RecruitmentStage.REPLACED.value,
        RecruitmentStage.REJECTED.value,
    }
    invalid_progress = next(
        (progress for progress in progress_items if progress.current_stage not in allowed_stages),
        None,
    )
    if invalid_progress is not None:
        raise BadRequestException("Onboarding fields can only be updated after screening is passed.")

    changed_count = 0
    updated_field_keys: set[str] = set()
    normalized_onboarding_status = onboarding_status.strip() or None if onboarding_status is not None else None
    normalized_salary_confirmed_at = salary_confirmed_at.strip() or None if salary_confirmed_at is not None else None
    normalized_gift_package_sent_at = gift_package_sent_at.strip() or None if gift_package_sent_at is not None else None
    milestone_timestamp = (
        _format_current_process_datetime() if normalized_onboarding_status in {"已进群", "已发大礼包"} else None
    )
    salary_confirmed_date = _format_current_process_date() if normalized_onboarding_status == "已发砍价" else None
    for progress in progress_items:
        next_data = dict(progress.data or {})
        changed_fields: dict[str, dict[str, Any]] = {}
        if has_onboarding_status_update:
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_STATUS.value)
            if previous_value != normalized_onboarding_status:
                if normalized_onboarding_status is None:
                    next_data.pop(JobProgressDataKey.ONBOARDING_STATUS.value, None)
                else:
                    next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = normalized_onboarding_status
                changed_fields[JobProgressDataKey.ONBOARDING_STATUS.value] = {
                    "from": previous_value,
                    "to": normalized_onboarding_status,
                }
        if has_onboarding_date_update:
            next_date = onboarding_date.isoformat() if onboarding_date is not None else None
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_DATE.value)
            if previous_value != next_date:
                if next_date is None:
                    next_data.pop(JobProgressDataKey.ONBOARDING_DATE.value, None)
                else:
                    next_data[JobProgressDataKey.ONBOARDING_DATE.value] = next_date
                changed_fields[JobProgressDataKey.ONBOARDING_DATE.value] = {
                    "from": previous_value,
                    "to": next_date,
                }
        if has_salary_confirmed_at_update:
            previous_value = next_data.get(JobProgressDataKey.SALARY_CONFIRMED_AT.value)
            if previous_value != normalized_salary_confirmed_at:
                if normalized_salary_confirmed_at is None:
                    next_data.pop(JobProgressDataKey.SALARY_CONFIRMED_AT.value, None)
                else:
                    next_data[JobProgressDataKey.SALARY_CONFIRMED_AT.value] = normalized_salary_confirmed_at
                changed_fields[JobProgressDataKey.SALARY_CONFIRMED_AT.value] = {
                    "from": previous_value,
                    "to": normalized_salary_confirmed_at,
                }
        if has_gift_package_sent_at_update:
            previous_value = next_data.get(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
            if previous_value != normalized_gift_package_sent_at:
                if normalized_gift_package_sent_at is None:
                    next_data.pop(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value, None)
                else:
                    next_data[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = normalized_gift_package_sent_at
                changed_fields[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = {
                    "from": previous_value,
                    "to": normalized_gift_package_sent_at,
                }
        if milestone_timestamp and normalized_onboarding_status == "已进群":
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_DATE.value)
            if previous_value != milestone_timestamp:
                next_data[JobProgressDataKey.ONBOARDING_DATE.value] = milestone_timestamp
                changed_fields[JobProgressDataKey.ONBOARDING_DATE.value] = {
                    "from": previous_value,
                    "to": milestone_timestamp,
                }
        if milestone_timestamp and normalized_onboarding_status == "已发大礼包":
            previous_value = next_data.get(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
            if previous_value != milestone_timestamp:
                next_data[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = milestone_timestamp
                changed_fields[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = {
                    "from": previous_value,
                    "to": milestone_timestamp,
                }
        if salary_confirmed_date and not _normalize_text(next_data.get(JobProgressDataKey.SALARY_CONFIRMED_AT.value)):
            next_data[JobProgressDataKey.SALARY_CONFIRMED_AT.value] = salary_confirmed_date
            changed_fields[JobProgressDataKey.SALARY_CONFIRMED_AT.value] = {
                "from": None,
                "to": salary_confirmed_date,
            }
        if not changed_fields:
            continue
        progress.data = next_data
        changed_count += 1
        updated_field_keys.update(changed_fields.keys())
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_NOTE_UPDATED.value,
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
    return JobProgressOnboardingUpdateResponse(
        updated_count=changed_count,
        updated_field_keys=sorted(updated_field_keys),
    ).model_dump()
