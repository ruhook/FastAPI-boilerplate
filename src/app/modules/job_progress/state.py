from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ConflictException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..job.model import Job
from .const import JobProgressDataKey
from .model import JobProgress
from .normalization import _normalize_text, _serialize_progress_datetime


async def _get_company_name_map_by_job_ids(
    *,
    job_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_job_ids = sorted({int(job_id) for job_id in job_ids if job_id})
    if not normalized_job_ids:
        return {}
    result = await db.execute(
        select(Job.id, AdminCompany.name)
        .outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
        .where(
            Job.id.in_(normalized_job_ids),
            Job.is_deleted.is_(False),
        )
    )
    return {int(job_id): company_name for job_id, company_name in result.all() if company_name}


async def _get_company_name_map_by_company_ids(
    *,
    company_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_company_ids = sorted({int(company_id) for company_id in company_ids if company_id})
    if not normalized_company_ids:
        return {}
    result = await db.execute(
        select(AdminCompany.id, AdminCompany.name).where(
            AdminCompany.id.in_(normalized_company_ids),
            AdminCompany.is_deleted.is_(False),
        )
    )
    return {int(company_id): company_name for company_id, company_name in result.all() if company_name}


async def _get_project_name_map_by_job_ids(
    *,
    job_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_job_ids = sorted({int(job_id) for job_id in job_ids if job_id})
    if not normalized_job_ids:
        return {}
    result = await db.execute(
        select(Job.id, AdminCompanyProject.name)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == Job.project_id)
        .where(
            Job.id.in_(normalized_job_ids),
            Job.is_deleted.is_(False),
        )
    )
    return {int(job_id): project_name for job_id, project_name in result.all() if project_name}


async def _get_project_name_map_by_project_ids(
    *,
    project_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_project_ids = sorted({int(project_id) for project_id in project_ids if project_id})
    if not normalized_project_ids:
        return {}
    result = await db.execute(
        select(AdminCompanyProject.id, AdminCompanyProject.name).where(
            AdminCompanyProject.id.in_(normalized_project_ids),
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    return {int(project_id): project_name for project_id, project_name in result.all() if project_name}


def _has_assessment_invitation(progress: JobProgress) -> bool:
    data = progress.data or {}
    return bool(
        _normalize_text(data.get(JobProgressDataKey.ASSESSMENT_INVITED_AT.value))
        or _normalize_text(data.get(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value))
    )


def _mark_assessment_invited(
    progress: JobProgress,
    *,
    invited_at: datetime | None = None,
    mail_task_id: int | None = None,
    sent_at: datetime | None = None,
) -> list[str]:
    next_data = dict(progress.data or {})
    changed_fields: list[str] = []
    marker_time = sent_at or invited_at or datetime.now(UTC)
    marker_value = _serialize_progress_datetime(marker_time)
    if not _normalize_text(next_data.get(JobProgressDataKey.ASSESSMENT_INVITED_AT.value)):
        next_data[JobProgressDataKey.ASSESSMENT_INVITED_AT.value] = marker_value
        changed_fields.append(JobProgressDataKey.ASSESSMENT_INVITED_AT.value)
    if sent_at is not None and next_data.get(JobProgressDataKey.ASSESSMENT_SENT_AT.value) != marker_value:
        next_data[JobProgressDataKey.ASSESSMENT_SENT_AT.value] = marker_value
        changed_fields.append(JobProgressDataKey.ASSESSMENT_SENT_AT.value)
    if (
        mail_task_id is not None
        and next_data.get(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value) != mail_task_id
    ):
        next_data[JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value] = mail_task_id
        changed_fields.append(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value)
    if changed_fields:
        progress.data = next_data
    return changed_fields


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
    expected_versions: dict[int, int] | None = None,
) -> list[JobProgress]:
    result = await db.execute(build_locked_job_progress_query(job_id=job_id, progress_ids=progress_ids))
    items = list(result.scalars().all())
    if len(items) != len(set(progress_ids)):
        raise NotFoundException("Job progress record not found.")
    ensure_expected_progress_versions(items, expected_versions=expected_versions)
    return items


def build_locked_job_progress_query(*, job_id: int, progress_ids: list[int]) -> Select[tuple[JobProgress]]:
    normalized_ids = sorted(set(progress_ids))
    return (
        select(JobProgress)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.id.in_(normalized_ids),
            JobProgress.is_deleted.is_(False),
        )
        .order_by(JobProgress.id.asc())
        .with_for_update()
    )


def ensure_expected_progress_versions(
    progress_items: list[JobProgress],
    *,
    expected_versions: dict[int, int] | None,
) -> None:
    if expected_versions is None:
        return
    for progress in progress_items:
        if expected_versions.get(progress.id) != progress.version:
            raise ConflictException("Job progress changed; refresh the list and retry.")
