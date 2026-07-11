from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..admin.admin_user.const import AdminAccountStatus
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.form_template.model import AdminFormTemplate
from ..job_progress.const import RecruitmentStage
from ..job_progress.model import JobProgress
from ..referral_bonus_model.model import ReferralBonusModel
from .const import JOB_DATA_COLLABORATORS_KEY, JOB_DATA_LANGUAGES_KEY
from .model import Job
from .policy import _is_assessment_reviewer_only, _job_list_order_by, can_edit_job, ensure_job_editable
from .schema import JobListItemRead, JobListPage, JobOwnerOptionRead
from .serialization import _build_compensation_label, serialize_job


async def list_job_owner_options(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AdminUser)
        .where(
            AdminUser.is_deleted.is_(False),
            AdminUser.status == AdminAccountStatus.ENABLED.value,
        )
        .order_by(AdminUser.name.asc(), AdminUser.username.asc(), AdminUser.id.asc())
    )
    return [
        JobOwnerOptionRead(
            id=admin_user.id,
            name=admin_user.name,
            username=admin_user.username,
            email=admin_user.email,
            status=admin_user.status,
        ).model_dump()
        for admin_user in result.scalars().all()
    ]


async def _ensure_form_template_exists(template_id: int, db: AsyncSession) -> None:
    result = await db.execute(
        select(AdminFormTemplate.id).where(
            AdminFormTemplate.id == template_id,
            AdminFormTemplate.is_deleted.is_(False),
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundException("Form template not found.")


def _get_admin_display_name(admin_user: AdminUser) -> str:
    return str(admin_user.name or admin_user.username or admin_user.email or "")


async def _get_enabled_admin_user(admin_user_id: int, db: AsyncSession) -> AdminUser:
    result = await db.execute(
        select(AdminUser).where(
            AdminUser.id == admin_user_id,
            AdminUser.is_deleted.is_(False),
            AdminUser.status == AdminAccountStatus.ENABLED.value,
        )
    )
    admin_user = result.scalar_one_or_none()
    if admin_user is None:
        raise NotFoundException("Owner admin account not found.")
    return admin_user


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


async def ensure_job_editable_for_admin(
    job_id: int,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any] | None,
) -> Job:
    job = await get_job_model(job_id, db)
    ensure_job_editable(job, current_admin)
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
        current_admin=current_admin,
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
    sort_by: str | None = None,
    sort_order: str | None = None,
    current_admin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conditions: list[Any] = [Job.is_deleted.is_(False)]
    base_query = (
        select(Job)
        .outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == Job.project_id)
    )
    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Job.title.ilike(term),
                AdminCompany.name.ilike(term),
                AdminCompanyProject.name.ilike(term),
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
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == Job.project_id)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        base_query.where(*conditions)
        .order_by(*_job_list_order_by(sort_by, sort_order))
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
            languages=list((job.data or {}).get(JOB_DATA_LANGUAGES_KEY) or []),
            owner_name=owner_name_map.get(job.owner_admin_user_id) or (job.data or {}).get("owner_name"),
            collaborators=list((job.data or {}).get(JOB_DATA_COLLABORATORS_KEY) or []),
            compensation=_build_compensation_label(job),
            assessment_enabled=job.assessment_enabled,
            can_edit=can_edit_job(job, current_admin),
        )
        for job in jobs
    ]
    return JobListPage(items=items, total=total, page=page, page_size=page_size).model_dump()
