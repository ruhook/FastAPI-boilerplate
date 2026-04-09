from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ForbiddenException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.form_template.model import AdminFormTemplate
from ..admin.mail_account.model import MailAccount
from ..admin.mail_signature.model import MailSignature
from ..admin.mail_template.model import MailTemplate
from ..job_progress.const import RecruitmentStage
from ..job_progress.model import JobProgress
from .const import (
    JOB_DATA_APPLICATION_SUMMARY_KEY,
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_COLLABORATORS_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_HIGHLIGHTS_KEY,
    JOB_DATA_PUBLISH_CHECKLIST_KEY,
    JOB_DATA_SCREENING_RULES_KEY,
)
from .model import Job
from .schema import (
    JobAssessmentConfig,
    JobCreate,
    JobFormStrategy,
    JobListItemRead,
    JobListPage,
    JobRead,
    JobUpdate,
)


def _normalize_decimal(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _build_compensation_label(job: Job) -> str:
    min_value = _normalize_decimal(job.compensation_min)
    max_value = _normalize_decimal(job.compensation_max)
    if min_value is None and max_value is None:
        return "-"
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".") if min_value is not None else "0"
    max_source = max_value if max_value is not None else min_value
    max_text = f"{max_source:.2f}".rstrip("0").rstrip(".") if max_source is not None else "0"
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


async def _ensure_form_template_exists(template_id: int, db: AsyncSession) -> None:
    result = await db.execute(
        select(AdminFormTemplate.id).where(
            AdminFormTemplate.id == template_id,
            AdminFormTemplate.is_deleted.is_(False),
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundException("Form template not found.")


async def _ensure_mail_dependencies_exist(
    assessment: JobAssessmentConfig,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> None:
    if not assessment.enabled:
        return

    account_result = await db.execute(
        select(MailAccount.id).where(
            MailAccount.id == assessment.mail_account_id,
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise NotFoundException("Mail account not found.")

    template_result = await db.execute(
        select(MailTemplate.id).where(
            MailTemplate.id == assessment.mail_template_id,
            MailTemplate.admin_user_id == admin_user_id,
            MailTemplate.is_deleted.is_(False),
        )
    )
    if template_result.scalar_one_or_none() is None:
        raise NotFoundException("Assessment mail template not found.")

    signature_result = await db.execute(
        select(MailSignature.id).where(
            MailSignature.id == assessment.mail_signature_id,
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.is_deleted.is_(False),
        )
    )
    if signature_result.scalar_one_or_none() is None:
        raise NotFoundException("Assessment mail signature not found.")


def _job_data_from_payload(
    payload: JobCreate | JobUpdate,
    *,
    owner_name: str | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if owner_name is not None:
        data["owner_name"] = owner_name
    data[JOB_DATA_COLLABORATORS_KEY] = payload.collaborators
    data[JOB_DATA_HIGHLIGHTS_KEY] = payload.highlights
    data[JOB_DATA_FORM_FIELDS_KEY] = [field.model_dump() for field in payload.form_fields]
    data[JOB_DATA_AUTOMATION_RULES_KEY] = payload.automation_rules.model_dump()
    data[JOB_DATA_SCREENING_RULES_KEY] = payload.screening_rules
    data[JOB_DATA_PUBLISH_CHECKLIST_KEY] = payload.publish_checklist
    data[JOB_DATA_APPLICATION_SUMMARY_KEY] = (
        payload.application_summary.model_dump() if payload.application_summary else None
    )
    return data


def _merge_job_data(
    current_data: dict[str, Any],
    payload: JobUpdate,
    *,
    owner_name: str | None,
) -> dict[str, Any]:
    next_data = dict(current_data or {})
    if owner_name is not None:
        next_data["owner_name"] = owner_name
    if payload.collaborators is not None:
        next_data[JOB_DATA_COLLABORATORS_KEY] = payload.collaborators
    if payload.highlights is not None:
        next_data[JOB_DATA_HIGHLIGHTS_KEY] = payload.highlights
    if payload.form_fields is not None:
        next_data[JOB_DATA_FORM_FIELDS_KEY] = [field.model_dump() for field in payload.form_fields]
    if payload.automation_rules is not None:
        next_data[JOB_DATA_AUTOMATION_RULES_KEY] = payload.automation_rules.model_dump()
    if payload.screening_rules is not None:
        next_data[JOB_DATA_SCREENING_RULES_KEY] = payload.screening_rules
    if payload.publish_checklist is not None:
        next_data[JOB_DATA_PUBLISH_CHECKLIST_KEY] = payload.publish_checklist
    if payload.application_summary is not None:
        next_data[JOB_DATA_APPLICATION_SUMMARY_KEY] = payload.application_summary.model_dump()
    return next_data


def serialize_job(job: Job, owner_name: str | None) -> dict[str, Any]:
    data = job.data or {}
    assessment_config = JobAssessmentConfig(
        enabled=job.assessment_enabled,
        mail_account_id=job.assessment_mail_account_id,
        mail_template_id=job.assessment_mail_template_id,
        mail_signature_id=job.assessment_mail_signature_id,
        mail_account_label=data.get("assessment_mail_account_label"),
        mail_template_name=data.get("assessment_mail_template_name"),
        mail_signature_name=data.get("assessment_mail_signature_name"),
    )
    return JobRead(
        id=job.id,
        title=job.title,
        company=job.company_name,
        country=job.country,
        status=job.status,
        work_mode=job.work_mode,
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        description=job.description,
        owner_name=owner_name or data.get("owner_name"),
        collaborators=list(data.get(JOB_DATA_COLLABORATORS_KEY) or []),
        form_strategy=JobFormStrategy(
            template_id=job.form_template_id,
        ),
        assessment_config=assessment_config,
        form_fields=list(data.get(JOB_DATA_FORM_FIELDS_KEY) or []),
        automation_rules=data.get(JOB_DATA_AUTOMATION_RULES_KEY) or {"combinator": "and", "rules": []},
        screening_rules=list(data.get(JOB_DATA_SCREENING_RULES_KEY) or []),
        publish_checklist=list(data.get(JOB_DATA_PUBLISH_CHECKLIST_KEY) or []),
        highlights=list(data.get(JOB_DATA_HIGHLIGHTS_KEY) or []),
        application_summary=data.get(JOB_DATA_APPLICATION_SUMMARY_KEY),
        applicant_count=job.applicant_count,
        owner_admin_user_id=job.owner_admin_user_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        data=data,
    ).model_dump()


async def _get_owner_name_map(db: AsyncSession, owner_ids: Sequence[int]) -> dict[int, str]:
    if not owner_ids:
        return {}
    result = await db.execute(
        select(AdminUser.id, AdminUser.name).where(AdminUser.id.in_(sorted(set(owner_ids))))
    )
    return {int(row[0]): row[1] for row in result.all()}


async def get_job_model(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    return job


async def get_job(job_id: int, db: AsyncSession) -> dict[str, Any]:
    job = await get_job_model(job_id, db)
    owner_name_map = await _get_owner_name_map(db, [job.owner_admin_user_id])
    return serialize_job(job, owner_name_map.get(job.owner_admin_user_id))


def _is_assessment_reviewer_only(current_admin: dict[str, Any] | None) -> bool:
    if not current_admin or current_admin.get("is_superuser"):
        return False
    permissions = set(current_admin.get("permissions") or [])
    return "测试题判题" in permissions and "岗位管理" not in permissions


async def _has_assessment_assignment(
    *,
    job_id: int,
    admin_user_id: int,
    db: AsyncSession,
) -> bool:
    result = await db.execute(
        select(JobProgress.id).where(
            JobProgress.job_id == job_id,
            JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
            JobProgress.assessment_reviewer_admin_user_id == admin_user_id,
            JobProgress.is_deleted.is_(False),
        )
        .limit(1)
    )
    return result.first() is not None


async def get_job_for_admin(
    job_id: int,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = await get_job_model(job_id, db)
    if _is_assessment_reviewer_only(current_admin):
        has_assignment = await _has_assessment_assignment(
            job_id=job_id,
            admin_user_id=int(current_admin["id"]),
            db=db,
        )
        if not has_assignment:
            raise ForbiddenException("You are not assigned to review this job.")
    owner_name_map = await _get_owner_name_map(db, [job.owner_admin_user_id])
    return serialize_job(job, owner_name_map.get(job.owner_admin_user_id))


async def list_jobs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
    company: str | None = None,
    country: str | None = None,
    current_admin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conditions = [Job.is_deleted.is_(False)]
    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Job.title.ilike(term),
                Job.company_name.ilike(term),
                Job.country.ilike(term),
            )
        )
    if status:
        conditions.append(Job.status == status)
    if company:
        conditions.append(Job.company_name == company)
    if country:
        conditions.append(Job.country == country)
    if _is_assessment_reviewer_only(current_admin):
        conditions.append(
            Job.id.in_(
                select(JobProgress.job_id).where(
                    JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
                    JobProgress.assessment_reviewer_admin_user_id == int(current_admin["id"]),
                    JobProgress.is_deleted.is_(False),
                )
            )
        )

    total_result = await db.execute(select(func.count()).select_from(Job).where(*conditions))
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        select(Job)
        .where(*conditions)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = result.scalars().all()
    owner_name_map = await _get_owner_name_map(db, [job.owner_admin_user_id for job in jobs])
    items = [
        JobListItemRead(
            id=job.id,
            title=job.title,
            company=job.company_name,
            country=job.country,
            status=job.status,
            applicants=job.applicant_count,
            created_at=job.created_at,
            work_mode=job.work_mode,
            owner_name=owner_name_map.get(job.owner_admin_user_id) or (job.data or {}).get("owner_name"),
            collaborators=list((job.data or {}).get(JOB_DATA_COLLABORATORS_KEY) or []),
            compensation=_build_compensation_label(job),
            assessment_enabled=job.assessment_enabled,
        ).model_dump()
        for job in jobs
    ]
    return JobListPage(items=items, total=total, page=page, page_size=page_size).model_dump()


async def create_job(
    payload: JobCreate,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any],
) -> dict[str, Any]:
    await _ensure_form_template_exists(payload.form_strategy.template_id, db)
    await _ensure_mail_dependencies_exist(
        payload.assessment_config,
        db,
        admin_user_id=int(current_admin["id"]),
    )

    owner_name = payload.owner_name or str(current_admin.get("name") or current_admin.get("username") or "")
    data = _job_data_from_payload(payload, owner_name=owner_name)
    data["assessment_mail_account_label"] = payload.assessment_config.mail_account_label
    data["assessment_mail_template_name"] = payload.assessment_config.mail_template_name
    data["assessment_mail_signature_name"] = payload.assessment_config.mail_signature_name

    job = Job(
        title=payload.title,
        company_name=payload.company,
        country=payload.country,
        status=payload.status,
        work_mode=payload.work_mode,
        compensation_min=payload.compensation_min,
        compensation_max=payload.compensation_max,
        compensation_unit=payload.compensation_unit,
        description=payload.description,
        owner_admin_user_id=int(current_admin["id"]),
        form_template_id=payload.form_strategy.template_id,
        assessment_enabled=payload.assessment_config.enabled,
        assessment_mail_account_id=payload.assessment_config.mail_account_id,
        assessment_mail_template_id=payload.assessment_config.mail_template_id,
        assessment_mail_signature_id=payload.assessment_config.mail_signature_id,
        applicant_count=0,
        data=data,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return serialize_job(job, owner_name)


async def update_job(
    job_id: int,
    payload: JobUpdate,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any],
) -> dict[str, Any]:
    job = await get_job_model(job_id, db)

    if payload.form_strategy is not None:
        await _ensure_form_template_exists(payload.form_strategy.template_id, db)
        job.form_template_id = payload.form_strategy.template_id

    if payload.assessment_config is not None:
        await _ensure_mail_dependencies_exist(
            payload.assessment_config,
            db,
            admin_user_id=int(current_admin["id"]),
        )
        job.assessment_enabled = payload.assessment_config.enabled
        job.assessment_mail_account_id = payload.assessment_config.mail_account_id if payload.assessment_config.enabled else None
        job.assessment_mail_template_id = payload.assessment_config.mail_template_id if payload.assessment_config.enabled else None
        job.assessment_mail_signature_id = payload.assessment_config.mail_signature_id if payload.assessment_config.enabled else None

    if payload.title is not None:
        job.title = payload.title
    if payload.company is not None:
        job.company_name = payload.company
    if payload.country is not None:
        job.country = payload.country
    if payload.status is not None:
        job.status = payload.status
    if payload.work_mode is not None:
        job.work_mode = payload.work_mode
    if payload.compensation_unit is not None:
        job.compensation_unit = payload.compensation_unit
    if payload.description is not None:
        job.description = payload.description
    if payload.compensation_min is not None:
        job.compensation_min = payload.compensation_min
    if payload.compensation_max is not None:
        job.compensation_max = payload.compensation_max

    next_owner_name = payload.owner_name
    if next_owner_name is None:
        next_owner_name = (job.data or {}).get("owner_name") or str(
            current_admin.get("name") or current_admin.get("username") or ""
        )

    next_data = _merge_job_data(job.data or {}, payload, owner_name=next_owner_name)
    if payload.assessment_config is not None:
        next_data["assessment_mail_account_label"] = payload.assessment_config.mail_account_label
        next_data["assessment_mail_template_name"] = payload.assessment_config.mail_template_name
        next_data["assessment_mail_signature_name"] = payload.assessment_config.mail_signature_name
    job.data = next_data

    job.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(job)
    return serialize_job(job, next_owner_name)
