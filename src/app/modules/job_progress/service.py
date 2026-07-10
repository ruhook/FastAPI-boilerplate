from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.internal_notification.service import create_admin_internal_notification
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_internal_notification.service import create_candidate_internal_notification
from ..contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_EXPIRED,
    CONTRACT_STATUS_TERMINATED,
)
from ..contract_record.model import ContractRecord
from ..contract_record.service import (
    get_current_contract_record_by_progress_id,
    get_default_contract_end_date,
    upsert_contract_record_for_progress,
)
from ..job.const import JOB_DATA_LANGUAGES_KEY
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..referral_bonus_model.service import ensure_user_referral_profile_from_job
from ..user.model import User
from .automation import _field_row_value, _resolve_initial_stage
from .const import (
    JobProgressDataKey,
    RecruitmentStage,
    get_allowed_recruitment_stage_transitions,
    get_recruitment_stage_cn_name,
)
from .language_rules import (
    normalize_progress_language_value,
    resolve_progress_language,
)
from .mail_workflow import _trigger_stage_mail_task
from .mail_workflow import notify_job_progress_sign_contract as notify_job_progress_sign_contract
from .mail_workflow import (
    sync_assessment_sent_at_from_mail_task as sync_assessment_sent_at_from_mail_task,
)
from .model import JobProgress
from .normalization import (
    _has_assessment_attachment,
    _normalize_decimal,
    _normalize_text,
)
from .queries import (
    get_candidate_job_application_detail as get_candidate_job_application_detail,
)
from .queries import (
    list_candidate_contracts as list_candidate_contracts,
)
from .queries import (
    list_candidate_job_applications as list_candidate_job_applications,
)
from .queries import (
    list_job_progress as list_job_progress,
)
from .rejection_restore import build_rejected_progress_data, pop_active_contract_restore_data
from .schema import (
    JobProgressAssessmentInviteMarkResponse,
    JobProgressAssessmentUploadResponse,
    JobProgressCandidateSignedContractUploadResponse,
    JobProgressCompanySealedContractUploadResponse,
    JobProgressContractDraftUploadResponse,
    JobProgressContractRecordUpdateItemRead,
    JobProgressContractRecordUpdateResponse,
    JobProgressOnboardingUpdateResponse,
)
from .serialization import (
    _extract_contract_record_asset_ids,
    _get_assessment_submission_records,
    _serialize_contract_record_data,
    _serialize_process_assets,
    _serialize_process_data,
)
from .serialization import (
    serialize_job_progress as serialize_job_progress,
)
from .state import (
    _has_assessment_invitation,
    _mark_assessment_invited,
)
from .state import (
    build_locked_job_progress_query as build_locked_job_progress_query,
)
from .state import (
    ensure_expected_progress_versions as ensure_expected_progress_versions,
)
from .state import (
    get_job_progress_by_application_id as get_job_progress_by_application_id,
)
from .state import (
    get_job_progress_models as get_job_progress_models,
)

CONTRACT_RECORD_FIELD_STAGE_MAP: dict[str, set[str]] = {
    "agreement_ref_no": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "rate": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "signing_status": {
        RecruitmentStage.SCREENING_PASSED.value,
    },
    "contract_review": {
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "end_date": {
        RecruitmentStage.CONTRACT_POOL.value,
    },
}


def _validate_contract_record_update_stage(*, stage: str, changed_fields: list[str]) -> None:
    unsupported_fields = sorted(
        {field for field in changed_fields if stage not in CONTRACT_RECORD_FIELD_STAGE_MAP.get(field, set())}
    )
    if unsupported_fields:
        stage_name = get_recruitment_stage_cn_name(stage)
        raise BadRequestException(f"Contract fields {', '.join(unsupported_fields)} cannot be updated in {stage_name}.")


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
    active_restore_data_map: dict[int, tuple[dict[str, Any], str, date | None]] = {}

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
                RecruitmentStage.ACTIVE.value,
            }
            if rejected_from_stage not in allowed_restore_stages:
                raise BadRequestException("Rejected progress record is missing a supported source stage.")
            if normalized_target_stage.value != rejected_from_stage:
                raise BadRequestException("Rejected progress record can only restore to its source stage.")
            if normalized_target_stage == RecruitmentStage.ACTIVE:
                progress_data = progress.data or {}
                if (
                    JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_STATUS.value not in progress_data
                    or JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_END_DATE.value not in progress_data
                ):
                    raise BadRequestException("Rejected active record is missing previous contract state.")
                try:
                    cleaned_data, previous_status, previous_end_date = pop_active_contract_restore_data(progress_data)
                except ValueError as exc:
                    raise BadRequestException("Rejected active record has invalid previous contract state.") from exc
                if not previous_status:
                    raise BadRequestException("Rejected active record is missing previous contract status.")
                active_restore_data_map[progress.id] = (
                    cleaned_data,
                    previous_status,
                    previous_end_date,
                )
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
            if contract_record.candidate_signed_contract_asset_id in (None, 0, ""):
                raise BadRequestException("Active stage requires a candidate signed contract.")
            current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
            if current_contract_review != "审核通过":
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
                or contract_record.contract_status == "Active"
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
            leaving_contract = leaving_active_contract_record_map.get(progress.id)
            next_data = build_rejected_progress_data(
                next_data,
                source_stage=from_stage,
                contract_status=leaving_contract.contract_status if leaving_contract is not None else None,
                contract_end_date=leaving_contract.end_date if leaving_contract is not None else None,
            )
        elif JobProgressDataKey.REJECTED_FROM_STAGE.value in next_data:
            if normalized_target_stage == RecruitmentStage.ACTIVE:
                next_data = active_restore_data_map[progress.id][0]
            else:
                next_data.pop(JobProgressDataKey.REJECTED_FROM_STAGE.value, None)
                next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_STATUS.value, None)
                next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_END_DATE.value, None)
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
            if from_stage == RecruitmentStage.REJECTED.value:
                _, previous_status, previous_end_date = active_restore_data_map[progress.id]
                contract_record.contract_status = previous_status
                contract_record.end_date = previous_end_date
            else:
                contract_record.contract_status = "Active"
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
            contract_record.contract_status = "Terminated"
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


async def mark_job_progress_assessment_invited(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    mail_task_id: int | None = None,
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    allowed_stages = {
        RecruitmentStage.PENDING_SCREENING.value,
        RecruitmentStage.ASSESSMENT_REVIEW.value,
    }
    invalid_progress = next(
        (progress for progress in progress_items if progress.current_stage not in allowed_stages),
        None,
    )
    if invalid_progress is not None:
        raise BadRequestException("Assessment invite can only be marked before screening is passed.")

    changed_count = 0
    updated_field_keys: set[str] = set()
    now = datetime.now(UTC)
    for progress in progress_items:
        changed_fields = _mark_assessment_invited(
            progress,
            invited_at=now,
            mail_task_id=mail_task_id,
            sent_at=sent_at,
        )
        if not changed_fields:
            continue
        changed_count += 1
        updated_field_keys.update(changed_fields)
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": progress.job_id,
                "target_stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                "target_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ASSESSMENT_REVIEW.value),
                "reason": "assessment_invite_marked",
                "mail_task_id": mail_task_id,
                "operator_admin_user_id": admin_user_id,
            },
        )

    await db.flush()
    return JobProgressAssessmentInviteMarkResponse(
        updated_count=changed_count,
        updated_field_keys=sorted(updated_field_keys),
    ).model_dump()


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
    qa_status: str | None = None,
    qa_feedback: str | None = None,
) -> dict[str, Any]:
    assessment_field_updates: dict[JobProgressDataKey, Any] = {}
    reviewer_field_updates: dict[JobProgressDataKey, Any] = {}
    qa_field_updates: dict[JobProgressDataKey, Any] = {}
    if assessment_result is not None:
        assessment_field_updates[JobProgressDataKey.ASSESSMENT_RESULT] = assessment_result
    if assessment_review_comment is not None:
        assessment_field_updates[JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT] = assessment_review_comment
    if assessment_reviewer is not None:
        reviewer_field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER] = assessment_reviewer
    if assessment_reviewer_admin_user_id is not None:
        reviewer_field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER_ADMIN_USER_ID] = assessment_reviewer_admin_user_id
    if qa_status is not None:
        qa_field_updates[JobProgressDataKey.QA_STATUS] = qa_status
    if qa_feedback is not None:
        qa_field_updates[JobProgressDataKey.QA_FEEDBACK] = qa_feedback

    field_updates = {**assessment_field_updates, **reviewer_field_updates, **qa_field_updates}

    if not field_updates:
        raise BadRequestException("At least one review field is required.")

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

    assessment_field_key_values = {key.value for key in assessment_field_updates}
    reviewer_field_key_values = {key.value for key in reviewer_field_updates}
    qa_field_key_values = {key.value for key in qa_field_updates}

    for progress in progress_items:
        if assessment_field_updates and progress.current_stage not in {
            RecruitmentStage.ASSESSMENT_REVIEW.value,
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.REJECTED.value,
        }:
            raise BadRequestException(
                "Only assessment review, screening passed, or rejected stage records can update review fields here."
            )
        if reviewer_field_updates and progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can update reviewer fields here.")
        if qa_field_updates and progress.current_stage not in {
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.REJECTED.value,
        }:
            raise BadRequestException("Only screening passed or rejected stage records can update QA here.")
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

        if "assessment_reviewer_admin_user_id" in changed_fields and assessment_reviewer_admin_user_id is not None:
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

        assessment_changed_fields = {
            key: value for key, value in changed_fields.items() if key in assessment_field_key_values
        }
        reviewer_changed_fields = {
            key: value for key, value in changed_fields.items() if key in reviewer_field_key_values
        }
        qa_changed_fields = {key: value for key, value in changed_fields.items() if key in qa_field_key_values}

        if assessment_changed_fields or reviewer_changed_fields:
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
                    "updated_fields": {
                        **assessment_changed_fields,
                        **reviewer_changed_fields,
                    },
                },
            )

        if qa_changed_fields:
            await create_operation_log(
                db=db,
                user_id=progress.user_id,
                job_id=progress.job_id,
                application_id=progress.application_id,
                talent_profile_id=progress.talent_profile_id,
                log_type=OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value,
                data={
                    "job_progress_id": progress.id,
                    "job_id": job.id,
                    "job_title": job.title,
                    "current_stage": progress.current_stage,
                    "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                    "operator_admin_user_id": admin_user_id,
                    "updated_fields": qa_changed_fields,
                },
            )

    await db.flush()
    return {
        "updated_count": len(progress_items),
        "updated_field_keys": updated_field_keys,
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


async def update_job_progress_contract_record(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    ensure_contract_record: bool = False,
    agreement_ref_no: str | None = None,
    signing_status: str | None = None,
    contract_review: str | None = None,
    rate: str | None = None,
    end_date: date | None = None,
    update_agreement_ref_no: bool = False,
    update_signing_status: bool = False,
    update_contract_review: bool = False,
    update_rate: bool = False,
    update_end_date: bool = False,
) -> dict[str, Any]:
    changed_fields: list[str] = []
    field_updates: dict[str, Any] = {}
    data_updates: dict[str, Any] = {}
    has_agreement_ref_no_update = update_agreement_ref_no or agreement_ref_no is not None
    has_rate_update = update_rate or rate is not None
    has_signing_status_update = update_signing_status or signing_status is not None
    has_contract_review_update = update_contract_review or contract_review is not None
    has_end_date_update = update_end_date or end_date is not None

    if has_agreement_ref_no_update:
        field_updates["agreement_ref_no"] = (agreement_ref_no or "").strip() or None
        changed_fields.append("agreement_ref_no")
    if has_rate_update:
        field_updates["rate"] = _normalize_decimal(rate)
        changed_fields.append("rate")
    if has_signing_status_update:
        data_updates["signing_status"] = (signing_status or "").strip() or None
        changed_fields.append("signing_status")
    if has_contract_review_update:
        data_updates["contract_review"] = (contract_review or "").strip() or None
        changed_fields.append("contract_review")
    if has_end_date_update:
        field_updates["end_date"] = end_date
        changed_fields.append("end_date")

    if not changed_fields and not ensure_contract_record:
        raise BadRequestException("At least one contract field is required.")

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
    updated_contract_records: dict[int, ContractRecord] = {}
    for progress in progress_items:
        if ensure_contract_record and progress.current_stage not in {
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.CONTRACT_POOL.value,
        }:
            raise BadRequestException("Contract record can only be supplemented in 筛选通过 or 合同库.")
        _validate_contract_record_update_stage(
            stage=progress.current_stage,
            changed_fields=changed_fields,
        )
        if data_updates.get("contract_review") == "审核通过":
            current_contract_record = await get_current_contract_record_by_progress_id(
                progress_id=progress.id,
                db=db,
            )
            if current_contract_record is None or current_contract_record.candidate_signed_contract_asset_id in (
                None,
                0,
                "",
            ):
                raise BadRequestException("Approved contract review requires a candidate signed contract.")

        contract_record = await upsert_contract_record_for_progress(
            progress=progress,
            job=job,
            db=db,
            admin_user_id=admin_user_id,
            field_updates=field_updates,
            data_updates=data_updates,
        )
        if data_updates.get("contract_review") == "审核通过":
            previous_stage = progress.current_stage
            activated_at = datetime.now(UTC)
            next_data = dict(progress.data or {})
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"
            progress.data = next_data
            progress.current_stage = RecruitmentStage.ACTIVE.value
            progress.entered_stage_at = activated_at
            contract_record.contract_status = CONTRACT_STATUS_ACTIVE
            contract_record.updated_by_admin_user_id = admin_user_id
            await ensure_user_referral_profile_from_job(
                user_id=int(progress.user_id),
                job=job,
                db=db,
                admin_user_id=admin_user_id,
                contract_record=contract_record,
            )
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
                    "from_stage": previous_stage,
                    "from_stage_cn_name": get_recruitment_stage_cn_name(previous_stage),
                    "to_stage": RecruitmentStage.ACTIVE.value,
                    "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ACTIVE.value),
                    "operator_admin_user_id": admin_user_id,
                    "reason": "contract_review_approved",
                },
            )
        updated_contract_records[progress.id] = contract_record
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": changed_fields,
                "contract_record_ensured": ensure_contract_record,
            },
        )

    asset_ids: set[int] = set()
    for record in updated_contract_records.values():
        asset_ids.update(_extract_contract_record_asset_ids(record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    await db.flush()
    return JobProgressContractRecordUpdateResponse(
        updated_count=len(progress_items),
        updated_field_keys=changed_fields,
        items=[
            JobProgressContractRecordUpdateItemRead(
                progress_id=progress.id,
                contract_record_data=_serialize_contract_record_data(
                    progress=progress,
                    contract_record=updated_contract_records.get(progress.id),
                    asset_map=asset_map,
                ),
            )
            for progress in progress_items
        ],
    ).model_dump()


async def submit_job_progress_assessment(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    assessment_suffix = Path((upload.filename or "").strip()).suffix.lower()
    if assessment_suffix not in {".xls", ".xlsx"}:
        raise BadRequestException("Only Excel files (.xls, .xlsx) are accepted for assessment uploads.")

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
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.PENDING_SCREENING.value,
                    RecruitmentStage.ASSESSMENT_REVIEW.value,
                ]
            ),
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Assessment upload record not found for this job.")
    if progress.current_stage == RecruitmentStage.PENDING_SCREENING.value and not _has_assessment_invitation(progress):
        raise BadRequestException("Assessment upload is available after the assessment invitation is sent.")

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
    previous_stage = progress.current_stage
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
    next_data.pop(JobProgressDataKey.ASSESSMENT_RESULT.value, None)
    next_data.pop(JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value, None)
    next_data.pop(JobProgressDataKey.QA_STATUS.value, None)
    progress.data = next_data

    if previous_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
        progress.current_stage = RecruitmentStage.ASSESSMENT_REVIEW.value
        progress.entered_stage_at = submitted_at
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": progress.job_id,
                "application_id": progress.application_id,
                "from_stage": previous_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(previous_stage),
                "to_stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ASSESSMENT_REVIEW.value),
                "reason": "候选人上传测试题，自动进入测试题回收。",
                "screening_mode": progress.screening_mode,
            },
        )

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
            "previous_stage": previous_stage,
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
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.SCREENING_PASSED.value,
                    RecruitmentStage.CONTRACT_POOL.value,
                ]
            ),
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Signed contract upload record not found for this job.")

    file_name = (upload.filename or "").strip().lower()
    if not file_name.endswith((".doc", ".docx")):
        raise BadRequestException("Signed contract must be uploaded as a .doc or .docx file.")

    progress_data = dict(progress.data or {})
    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db, for_update=True)
    if contract_record is None or contract_record.draft_contract_asset_id in (None, "", 0):
        raise BadRequestException("Draft contract is not available yet.")
    if contract_record.contract_status in {CONTRACT_STATUS_TERMINATED, CONTRACT_STATUS_EXPIRED}:
        raise BadRequestException("Contract signing is no longer available because this contract is inactive.")

    current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
    if (
        progress.current_stage == RecruitmentStage.CONTRACT_POOL.value
        and contract_record.candidate_signed_contract_asset_id not in (None, "", 0)
        and current_contract_review != "待修改"
    ):
        raise BadRequestException(
            "Your signed contract is currently under review. "
            "You can upload a new version after the review status changes to Needs Revision."
        )

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
    from_stage = progress.current_stage
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
            "submitted_contract_at": submitted_at.isoformat(),
        },
    )

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        field_updates={
            "candidate_signed_contract_asset_id": int(asset_payload["id"]),
            "parse_status": "pending",
            "parse_error": None,
        },
        data_updates={
            "source": "single_signed_upload",
            "candidate_signed_contract_attachment_name": asset_payload["original_name"],
            "candidate_signed_contract_submitted_at": submitted_at.isoformat(),
            "contract_review": "待审核",
        },
    )

    if from_stage == RecruitmentStage.SCREENING_PASSED.value:
        progress.current_stage = RecruitmentStage.CONTRACT_POOL.value
        progress.entered_stage_at = submitted_at
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
                "to_stage": RecruitmentStage.CONTRACT_POOL.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.CONTRACT_POOL.value),
                "reason": "candidate_signed_contract_submitted",
            },
        )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCandidateSignedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        candidate_signed_contract_asset=asset_payload,
        process_data=_serialize_process_data(progress_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
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
        select(JobProgress)
        .where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
        .with_for_update()
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

    current_process_data = dict(progress.data or {})

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

    uploaded_at = datetime.now(UTC)
    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates={
            "draft_contract_asset_id": int(asset_payload["id"]),
            "effective_date": uploaded_at.date(),
        },
        data_updates={
            "source": "single_draft_upload",
            "draft_contract_attachment_name": asset_payload["original_name"],
            "draft_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressContractDraftUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        contract_draft_asset=asset_payload,
        process_data=_serialize_process_data(current_process_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(current_process_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
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
        select(JobProgress)
        .where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
        .with_for_update()
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.CONTRACT_POOL.value,
        RecruitmentStage.ACTIVE.value,
    }:
        raise BadRequestException("Company signed contract can only be uploaded in 合同库 or Active.")

    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db, for_update=True)
    if contract_record is None:
        raise BadRequestException("Company signed contract requires a contract record.")
    if contract_record.candidate_signed_contract_asset_id in (None, 0, ""):
        raise BadRequestException(
            "Company signed contract can only be uploaded after the candidate signed contract is submitted."
        )

    current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
    if current_contract_review != "审核通过":
        raise BadRequestException("Company signed contract can only be uploaded after contract review is approved.")

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
    uploaded_at = datetime.now(UTC)
    from_stage = progress.current_stage

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

    field_updates: dict[str, Any] = {
        "company_sealed_contract_asset_id": int(asset_payload["id"]),
        "contract_attachment_asset_id": int(asset_payload["id"]),
    }
    if contract_record.effective_date is None:
        field_updates["effective_date"] = uploaded_at.date()

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates=field_updates,
        data_updates={
            "source": "single_company_sealed_upload",
            "company_sealed_contract_attachment_name": asset_payload["original_name"],
            "company_sealed_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )
    next_progress_data = dict(progress.data or {})
    next_progress_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"
    progress.data = next_progress_data
    if progress.current_stage != RecruitmentStage.ACTIVE.value:
        progress.current_stage = RecruitmentStage.ACTIVE.value
        progress.entered_stage_at = uploaded_at
    contract_record.contract_status = CONTRACT_STATUS_ACTIVE
    contract_record.updated_by_admin_user_id = admin_user_id
    await ensure_user_referral_profile_from_job(
        user_id=int(progress.user_id),
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        contract_record=contract_record,
    )
    if from_stage != RecruitmentStage.ACTIVE.value:
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
                "to_stage": RecruitmentStage.ACTIVE.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ACTIVE.value),
                "reason": "company_sealed_contract_uploaded",
                "operator_admin_user_id": admin_user_id,
            },
        )
    await create_candidate_internal_notification(
        db=db,
        recipient_user_id=progress.user_id,
        sender_admin_user_id=admin_user_id,
        category="contract_company_signed",
        title="Your contract is ready",
        description=f"The company countersigned contract for {job.title} is ready. You can view it in My Contracts.",
        action_url=f"/my-jobs/{progress.application_id}",
        data={
            "job_id": job.id,
            "job_title": job.title,
            "job_progress_id": progress.id,
            "application_id": progress.application_id,
            "contract_record_id": contract_record.id,
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
        },
    )
    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCompanySealedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        company_sealed_contract_asset=asset_payload,
        process_data=_serialize_process_data(progress.data or {}, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress.data or {}, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()
