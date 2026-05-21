from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.form_template.model import AdminFormTemplate
from ..admin.mail_account.model import MailAccount
from ..admin.mail_signature.model import MailSignature
from ..admin.mail_template.model import MailTemplate
from ..admin.role.const import is_assessment_reviewer_only_permissions
from ..job_progress.const import RecruitmentStage
from ..job_progress.model import JobProgress
from ..referral_bonus_model.model import ReferralBonusModel
from ..referral_bonus_model.service import ensure_referral_bonus_model
from .const import (
    JOB_DATA_APPLICATION_SUMMARY_KEY,
    JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY,
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_COLLABORATORS_KEY,
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_HIGHLIGHTS_KEY,
    JOB_DATA_PUBLISH_CHECKLIST_KEY,
    JOB_DATA_REJECTION_MAIL_CONFIG_KEY,
    JOB_DATA_SCREENING_RULES_KEY,
    JOB_DATA_SHOW_COMPENSATION_KEY,
)
from .model import Job
from .schema import (
    JobAssessmentConfig,
    JobCreate,
    JobFormStrategy,
    JobListItemRead,
    JobListPage,
    JobRead,
    JobRejectionMailConfig,
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


async def _resolve_company_snapshot(
    *,
    company_id: int | None,
    db: AsyncSession,
) -> int:
    if company_id is not None:
        result = await db.execute(
            select(AdminCompany.id).where(
                AdminCompany.id == company_id,
                AdminCompany.is_deleted.is_(False),
            )
        )
        resolved_id = result.scalar_one_or_none()
        if resolved_id is None:
            raise NotFoundException("Company not found.")
        return int(resolved_id)

    raise BadRequestException("Company selection is required.")


async def _resolve_project_selection(
    *,
    company_id: int,
    project_id: int | None,
    db: AsyncSession,
) -> int:
    if project_id is None:
        raise BadRequestException("Project selection is required.")

    result = await db.execute(
        select(AdminCompanyProject.id).where(
            AdminCompanyProject.id == project_id,
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    resolved_id = result.scalar_one_or_none()
    if resolved_id is None:
        raise NotFoundException("Project not found.")
    return int(resolved_id)


async def _get_company_name_map(
    db: AsyncSession,
    company_ids: Sequence[int],
) -> dict[int, str]:
    normalized_ids = sorted({int(company_id) for company_id in company_ids if company_id})
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(AdminCompany.id, AdminCompany.name).where(
            AdminCompany.id.in_(normalized_ids),
            AdminCompany.is_deleted.is_(False),
        )
    )
    return {int(company_id): company_name for company_id, company_name in result.all()}


async def _get_project_name_map(
    db: AsyncSession,
    project_ids: Sequence[int],
) -> dict[int, str]:
    normalized_ids = sorted({int(project_id) for project_id in project_ids if project_id})
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(AdminCompanyProject.id, AdminCompanyProject.name).where(
            AdminCompanyProject.id.in_(normalized_ids),
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    return {int(project_id): project_name for project_id, project_name in result.all()}


async def _ensure_mail_dependencies_exist(
    *,
    enabled: bool,
    mail_account_id: int | None,
    mail_template_id: int | None,
    mail_signature_id: int | None,
    config_label: str,
    db: AsyncSession,
    admin_user_id: int,
) -> None:
    if not enabled:
        return

    account_result = await db.execute(
        select(MailAccount.id).where(
            MailAccount.id == mail_account_id,
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise NotFoundException("Mail account not found.")

    template_result = await db.execute(
        select(MailTemplate.id)
        .outerjoin(AdminUser, AdminUser.id == MailTemplate.admin_user_id)
        .where(
            MailTemplate.id == mail_template_id,
            MailTemplate.is_deleted.is_(False),
            or_(
                MailTemplate.admin_user_id == admin_user_id,
                AdminUser.is_superuser.is_(True),
            ),
        )
    )
    if template_result.scalar_one_or_none() is None:
        raise NotFoundException(f"{config_label} mail template not found.")

    signature_result = await db.execute(
        select(MailSignature.id).where(
            MailSignature.id == mail_signature_id,
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.is_deleted.is_(False),
        )
    )
    if signature_result.scalar_one_or_none() is None:
        raise NotFoundException(f"{config_label} mail signature not found.")


def _build_rejection_mail_config(data: dict[str, Any]) -> JobRejectionMailConfig:
    raw = data.get(JOB_DATA_REJECTION_MAIL_CONFIG_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return JobRejectionMailConfig(
        enabled=bool(raw.get("enabled", False)),
        mail_account_id=raw.get("mail_account_id"),
        mail_template_id=raw.get("mail_template_id"),
        mail_signature_id=raw.get("mail_signature_id"),
        mail_account_label=raw.get("mail_account_label"),
        mail_template_name=raw.get("mail_template_name"),
        mail_signature_name=raw.get("mail_signature_name"),
    )


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
    data[JOB_DATA_SHOW_COMPENSATION_KEY] = payload.show_compensation
    data[JOB_DATA_CONTRACT_EXAMPLE_KEY] = payload.contract_example or ""
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
    if payload.show_compensation is not None:
        next_data[JOB_DATA_SHOW_COMPENSATION_KEY] = payload.show_compensation
    if payload.contract_example is not None:
        next_data[JOB_DATA_CONTRACT_EXAMPLE_KEY] = payload.contract_example
    return next_data


def serialize_job(
    job: Job,
    owner_name: str | None,
    company_name: str,
    project_name: str,
    referral_bonus_model_name: str | None = None,
) -> dict[str, Any]:
    data = job.data or {}
    assessment_config = JobAssessmentConfig(
        enabled=job.assessment_enabled,
        mail_account_id=job.assessment_mail_account_id,
        mail_template_id=job.assessment_mail_template_id,
        mail_signature_id=job.assessment_mail_signature_id,
        mail_account_label=data.get("assessment_mail_account_label"),
        mail_template_name=data.get("assessment_mail_template_name"),
        mail_signature_name=data.get("assessment_mail_signature_name"),
        assessment_external_url=data.get(JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY) or data.get("assessment_link"),
    )
    rejection_mail_config = _build_rejection_mail_config(data)
    return JobRead(
        id=job.id,
        title=job.title,
        company=company_name,
        company_id=job.company_id,
        project=project_name,
        project_id=job.project_id,
        referral_bonus_model_id=job.referral_bonus_model_id,
        referral_bonus_model_name=referral_bonus_model_name,
        country=job.country,
        status=job.status,
        work_mode=job.work_mode,
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        show_compensation=bool(data.get(JOB_DATA_SHOW_COMPENSATION_KEY, True)),
        description=job.description,
        contract_example=data.get(JOB_DATA_CONTRACT_EXAMPLE_KEY) or "",
        owner_name=owner_name or data.get("owner_name"),
        collaborators=list(data.get(JOB_DATA_COLLABORATORS_KEY) or []),
        form_strategy=JobFormStrategy(
            template_id=job.form_template_id,
        ),
        assessment_config=assessment_config,
        rejection_mail_config=rejection_mail_config,
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
    result = await db.execute(select(AdminUser.id, AdminUser.name).where(AdminUser.id.in_(sorted(set(owner_ids)))))
    return {int(row[0]): row[1] for row in result.all()}


async def _get_referral_bonus_model_name_map(db: AsyncSession, model_ids: Sequence[int]) -> dict[int, str]:
    normalized_ids = sorted({int(model_id) for model_id in model_ids if model_id})
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(ReferralBonusModel.id, ReferralBonusModel.name).where(ReferralBonusModel.id.in_(normalized_ids))
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
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    referral_bonus_model_name_map = await _get_referral_bonus_model_name_map(db, [job.referral_bonus_model_id])
    return serialize_job(
        job,
        owner_name_map.get(job.owner_admin_user_id),
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model_name_map.get(job.referral_bonus_model_id),
    )


def _is_assessment_reviewer_only(current_admin: dict[str, Any] | None) -> bool:
    if not current_admin:
        return False
    return is_assessment_reviewer_only_permissions(
        current_admin.get("permissions") or [],
        is_superuser=bool(current_admin.get("is_superuser")),
    )


async def _has_assessment_review_scope(
    *,
    job_id: int,
    db: AsyncSession,
) -> bool:
    result = await db.execute(
        select(JobProgress.id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
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
        has_assessment_scope = await _has_assessment_review_scope(
            job_id=job_id,
            db=db,
        )
        if not has_assessment_scope:
            raise ForbiddenException("This job has no assessment review records.")
    owner_name_map = await _get_owner_name_map(db, [job.owner_admin_user_id])
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    referral_bonus_model_name_map = await _get_referral_bonus_model_name_map(db, [job.referral_bonus_model_id])
    return serialize_job(
        job,
        owner_name_map.get(job.owner_admin_user_id),
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model_name_map.get(job.referral_bonus_model_id),
    )


async def list_jobs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
    company_id: int | None = None,
    country: str | None = None,
    current_admin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conditions = [Job.is_deleted.is_(False)]
    base_query = select(Job).outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Job.title.ilike(term),
                AdminCompany.name.ilike(term),
                Job.country.ilike(term),
            )
        )
    if status:
        conditions.append(Job.status == status)
    if company_id is not None:
        conditions.append(Job.company_id == company_id)
    if country:
        conditions.append(Job.country == country)
    if _is_assessment_reviewer_only(current_admin):
        conditions.append(
            Job.id.in_(
                select(JobProgress.job_id).where(
                    JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
                    JobProgress.is_deleted.is_(False),
                )
            )
        )

    total_result = await db.execute(
        select(func.count())
        .select_from(Job)
        .outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        base_query.where(*conditions)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = result.scalars().all()
    owner_name_map = await _get_owner_name_map(db, [job.owner_admin_user_id for job in jobs])
    company_name_map = await _get_company_name_map(db, [job.company_id for job in jobs])
    project_name_map = await _get_project_name_map(db, [job.project_id for job in jobs])
    referral_bonus_model_name_map = await _get_referral_bonus_model_name_map(
        db,
        [job.referral_bonus_model_id for job in jobs],
    )
    items = [
        JobListItemRead(
            id=job.id,
            title=job.title,
            company=company_name_map.get(job.company_id, "-"),
            company_id=job.company_id,
            project=project_name_map.get(job.project_id, "-"),
            project_id=job.project_id,
            referral_bonus_model_id=job.referral_bonus_model_id,
            referral_bonus_model_name=referral_bonus_model_name_map.get(job.referral_bonus_model_id),
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
    company_id = await _resolve_company_snapshot(
        company_id=payload.company_id,
        db=db,
    )
    project_id = await _resolve_project_selection(
        company_id=company_id,
        project_id=payload.project_id,
        db=db,
    )
    referral_bonus_model = await ensure_referral_bonus_model(
        model_id=payload.referral_bonus_model_id,
        db=db,
        active_only=True,
    )
    await _ensure_mail_dependencies_exist(
        enabled=payload.assessment_config.enabled,
        mail_account_id=payload.assessment_config.mail_account_id,
        mail_template_id=payload.assessment_config.mail_template_id,
        mail_signature_id=payload.assessment_config.mail_signature_id,
        config_label="Assessment",
        db=db,
        admin_user_id=int(current_admin["id"]),
    )
    await _ensure_mail_dependencies_exist(
        enabled=payload.rejection_mail_config.enabled,
        mail_account_id=payload.rejection_mail_config.mail_account_id,
        mail_template_id=payload.rejection_mail_config.mail_template_id,
        mail_signature_id=payload.rejection_mail_config.mail_signature_id,
        config_label="Rejection",
        db=db,
        admin_user_id=int(current_admin["id"]),
    )

    owner_name = payload.owner_name or str(current_admin.get("name") or current_admin.get("username") or "")
    data = _job_data_from_payload(payload, owner_name=owner_name)
    data["assessment_mail_account_label"] = payload.assessment_config.mail_account_label
    data["assessment_mail_template_name"] = payload.assessment_config.mail_template_name
    data["assessment_mail_signature_name"] = payload.assessment_config.mail_signature_name
    data[JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY] = payload.assessment_config.assessment_external_url
    data[JOB_DATA_REJECTION_MAIL_CONFIG_KEY] = payload.rejection_mail_config.model_dump()

    job = Job(
        title=payload.title,
        company_id=company_id,
        project_id=project_id,
        referral_bonus_model_id=referral_bonus_model.id,
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
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    return serialize_job(
        job,
        owner_name,
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model.name,
    )


async def update_job(
    job_id: int,
    payload: JobUpdate,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any],
) -> dict[str, Any]:
    job = await get_job_model(job_id, db)
    next_company_id = job.company_id
    next_project_id = job.project_id

    if payload.form_strategy is not None:
        await _ensure_form_template_exists(payload.form_strategy.template_id, db)
        job.form_template_id = payload.form_strategy.template_id

    if payload.assessment_config is not None:
        await _ensure_mail_dependencies_exist(
            enabled=payload.assessment_config.enabled,
            mail_account_id=payload.assessment_config.mail_account_id,
            mail_template_id=payload.assessment_config.mail_template_id,
            mail_signature_id=payload.assessment_config.mail_signature_id,
            config_label="Assessment",
            db=db,
            admin_user_id=int(current_admin["id"]),
        )
        job.assessment_enabled = payload.assessment_config.enabled
        job.assessment_mail_account_id = (
            payload.assessment_config.mail_account_id if payload.assessment_config.enabled else None
        )
        job.assessment_mail_template_id = (
            payload.assessment_config.mail_template_id if payload.assessment_config.enabled else None
        )
        job.assessment_mail_signature_id = (
            payload.assessment_config.mail_signature_id if payload.assessment_config.enabled else None
        )

    if payload.rejection_mail_config is not None:
        await _ensure_mail_dependencies_exist(
            enabled=payload.rejection_mail_config.enabled,
            mail_account_id=payload.rejection_mail_config.mail_account_id,
            mail_template_id=payload.rejection_mail_config.mail_template_id,
            mail_signature_id=payload.rejection_mail_config.mail_signature_id,
            config_label="Rejection",
            db=db,
            admin_user_id=int(current_admin["id"]),
        )

    if payload.company_id is not None:
        next_company_id = await _resolve_company_snapshot(
            company_id=payload.company_id,
            db=db,
        )

    if payload.project_id is not None:
        next_project_id = await _resolve_project_selection(
            company_id=next_company_id,
            project_id=payload.project_id,
            db=db,
        )
    elif payload.company_id is not None and next_company_id != job.company_id:
        raise BadRequestException("Project selection is required when company changes.")

    if payload.referral_bonus_model_id is not None and int(payload.referral_bonus_model_id) != int(
        job.referral_bonus_model_id
    ):
        referral_bonus_model = await ensure_referral_bonus_model(
            model_id=payload.referral_bonus_model_id,
            db=db,
            active_only=True,
        )
        job.referral_bonus_model_id = referral_bonus_model.id

    if payload.title is not None:
        job.title = payload.title
    if payload.company_id is not None:
        job.company_id = next_company_id
    if payload.project_id is not None:
        job.project_id = next_project_id
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
    if "compensation_min" in payload.model_fields_set:
        job.compensation_min = payload.compensation_min
    if "compensation_max" in payload.model_fields_set:
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
        next_data[JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY] = payload.assessment_config.assessment_external_url
    if payload.rejection_mail_config is not None:
        next_data[JOB_DATA_REJECTION_MAIL_CONFIG_KEY] = payload.rejection_mail_config.model_dump()
    job.data = next_data

    job.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(job)
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    referral_bonus_model_name_map = await _get_referral_bonus_model_name_map(db, [job.referral_bonus_model_id])
    return serialize_job(
        job,
        next_owner_name,
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model_name_map.get(job.referral_bonus_model_id),
    )
