from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.internal_notification.service import create_admin_internal_notification
from ..assets.schema import AssetUploadPayload
from ..assets.service import upload_asset
from ..candidate_application.model import CandidateApplication
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..user.model import User
from .const import JobProgressDataKey, RecruitmentStage, get_recruitment_stage_cn_name
from .model import JobProgress
from .schema import JobProgressAssessmentInviteMarkResponse, JobProgressAssessmentUploadResponse
from .serialization import _get_assessment_submission_records, _serialize_process_data
from .state import _has_assessment_invitation, _mark_assessment_invited, get_job_progress_models


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

    return JobProgressAssessmentUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        assessment_asset=asset_payload,
        process_data=_serialize_process_data(next_data, {int(asset_payload["id"]): asset_payload}),
        process_assets={},
    ).model_dump()
