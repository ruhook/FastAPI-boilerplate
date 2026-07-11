from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_field.const import CandidateFieldKey
from ..job.const import JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..job.model import Job
from ..job_progress.commands import move_job_progress_stage
from ..job_progress.const import JobProgressDataKey, RecruitmentScreeningMode, RecruitmentStage
from ..job_progress.model import JobProgress
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..user.model import User
from .pool_fields import (
    TALENT_STATUS_REPLACED,
    load_talent_pool_sources,
    validate_manual_talent_status,
)
from .queries import _get_talent_profile_model, _serialize_talent_profile
from .schema import TalentJoinJobResponse


async def join_talent_to_job(
    *,
    talent_id: int,
    job_id: int,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    job = (
        await db.scalars(
            select(Job)
            .where(Job.id == job_id, Job.is_deleted.is_(False))
            .with_for_update()
        )
    ).one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    if job.status != JobStatus.OPEN.value:
        raise BadRequestException("Only an open job can accept new talent.")

    existing_application_id = await db.scalar(
        select(CandidateApplication.id).where(
            CandidateApplication.user_id == talent.user_id,
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    if existing_application_id is not None:
        raise BadRequestException("This talent has already joined the job.")

    user = await db.get(User, int(talent.user_id))
    if user is None or user.is_deleted:
        raise NotFoundException("Candidate user not found.")

    now = datetime.now(UTC)
    application = CandidateApplication(
        user_id=int(talent.user_id),
        job_id=int(job.id),
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        status="submitted",
        submitted_at=now,
        data={"source": "admin_talent_join", "operator_admin_user_id": int(current_admin["id"])},
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise BadRequestException("This talent has already joined the job.") from exc

    value_by_key: dict[str, tuple[str | None, int | None]] = {
        CandidateFieldKey.FULL_NAME.value: (talent.full_name or user.name, None),
        CandidateFieldKey.EMAIL.value: (talent.email or user.email, None),
        CandidateFieldKey.WHATSAPP.value: (talent.whatsapp, None),
        CandidateFieldKey.NATIONALITY.value: (talent.nationality, None),
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value: (talent.location, None),
        CandidateFieldKey.NATIVE_LANGUAGES.value: (talent.native_languages, None),
        CandidateFieldKey.EDUCATION_STATUS.value: (talent.education, None),
        CandidateFieldKey.RESUME_ATTACHMENT.value: (None, talent.resume_asset_id),
    }
    field_rows: list[CandidateApplicationFieldValue] = []
    for sort_order, raw_field in enumerate((job.data or {}).get(JOB_DATA_FORM_FIELDS_KEY) or []):
        if not isinstance(raw_field, dict):
            continue
        field_key = str(raw_field.get("key") or "").strip()
        if not field_key or field_key not in value_by_key:
            continue
        display_value, asset_id = value_by_key[field_key]
        if display_value in (None, "") and asset_id is None:
            continue
        field_row = CandidateApplicationFieldValue(
            application_id=application.id,
            field_key=field_key,
            field_label=str(raw_field.get("label") or field_key),
            field_type=str(raw_field.get("type") or "text"),
            catalog_key=field_key,
            raw_value=display_value,
            display_value=display_value,
            asset_id=asset_id,
            sort_order=sort_order,
        )
        db.add(field_row)
        field_rows.append(field_row)

    progress = JobProgress(
        job_id=int(job.id),
        user_id=int(talent.user_id),
        application_id=int(application.id),
        talent_profile_id=int(talent.id),
        current_stage=RecruitmentStage.PENDING_SCREENING.value,
        screening_mode=RecruitmentScreeningMode.MANUAL.value,
        entered_stage_at=now,
        data={"source": "admin_talent_join"},
    )
    db.add(progress)
    await db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(applicant_count=Job.applicant_count + 1)
        .execution_options(synchronize_session=False)
    )
    talent.latest_applied_job_id = int(job.id)
    talent.latest_applied_job_title = job.title
    talent.latest_applied_at = now
    await db.flush()

    await create_operation_log(
        db=db,
        user_id=int(talent.user_id),
        job_id=int(job.id),
        application_id=int(application.id),
        talent_profile_id=int(talent.id),
        log_type=OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value,
        data={
            "application_id": application.id,
            "job_id": job.id,
            "job_title": job.title,
            "source": "admin_talent_join",
            "operator_admin_user_id": int(current_admin["id"]),
        },
    )
    await create_operation_log(
        db=db,
        user_id=int(talent.user_id),
        job_id=int(job.id),
        application_id=int(application.id),
        talent_profile_id=int(talent.id),
        log_type=OperationLogType.JOB_PROGRESS_CREATED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": job.id,
            "job_title": job.title,
            "current_stage": progress.current_stage,
            "source": "admin_talent_join",
            "operator_admin_user_id": int(current_admin["id"]),
        },
    )
    await db.flush()
    return TalentJoinJobResponse(
        talent_profile_id=int(talent.id),
        application_id=int(application.id),
        job_progress_id=int(progress.id),
        job_id=int(job.id),
        current_stage=progress.current_stage,
    ).model_dump()


async def update_talent_pool_note(
    *,
    talent_id: int,
    note: str | None,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    normalized_note = (note or "").strip() or None
    if progress is not None:
        progress.data = {
            **(progress.data or {}),
            JobProgressDataKey.NOTE.value: normalized_note,
        }
    talent.note = normalized_note
    await db.flush()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)


async def update_talent_pool_status(
    *,
    talent_id: int,
    status: str,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    try:
        normalized_status = validate_manual_talent_status(status, progress)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    if normalized_status == TALENT_STATUS_REPLACED and progress is not None:
        await move_job_progress_stage(
            job_id=int(progress.job_id),
            progress_ids=[int(progress.id)],
            target_stage=RecruitmentStage.REPLACED.value,
            admin_user_id=int(current_admin["id"]),
            reason="talent_pool_status_replaced",
            expected_versions={int(progress.id): int(progress.version)},
            db=db,
        )
    talent.status_override = normalized_status
    await db.flush()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)
