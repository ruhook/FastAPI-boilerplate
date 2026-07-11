from typing import Any

from sqlalchemy import func

from ...core.exceptions.http_exceptions import BadRequestException, ForbiddenException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.role.const import is_assessment_reviewer_only_permissions
from .model import Job


def can_edit_job(job: Job, current_admin: dict[str, Any] | None) -> bool:
    if not current_admin:
        return False
    admin_id = current_admin.get("id")
    if admin_id is None:
        return False
    return int(job.owner_admin_user_id) == int(admin_id)


JOB_LIST_SORT_FIELDS = {
    "title",
    "company",
    "project",
    "country",
    "compensation",
    "applicants",
    "status",
    "createdAt",
}


def _normalize_job_sort_by(value: str | None) -> str | None:
    sort_by = (value or "").strip()
    if not sort_by:
        return None
    if sort_by not in JOB_LIST_SORT_FIELDS:
        raise BadRequestException("Invalid job sort field.")
    return sort_by


def _normalize_job_sort_order(value: str | None) -> str:
    sort_order = (value or "ascend").strip().casefold()
    if sort_order in {"asc", "ascend"}:
        return "ascend"
    if sort_order in {"desc", "descend"}:
        return "descend"
    raise BadRequestException("Invalid job sort order.")


def _job_list_order_by(sort_by: str | None, sort_order: str | None) -> list[Any]:
    normalized_sort_by = _normalize_job_sort_by(sort_by)
    if normalized_sort_by is None:
        return [Job.created_at.desc(), Job.id.desc()]

    normalized_sort_order = _normalize_job_sort_order(sort_order)

    def direction(expression: Any) -> Any:
        return expression.desc() if normalized_sort_order == "descend" else expression.asc()

    columns: list[Any]
    if normalized_sort_by == "title":
        columns = [Job.title]
    elif normalized_sort_by == "company":
        columns = [func.coalesce(AdminCompany.name, ""), Job.title]
    elif normalized_sort_by == "project":
        columns = [func.coalesce(AdminCompanyProject.name, ""), Job.title]
    elif normalized_sort_by == "country":
        columns = [Job.country, Job.title]
    elif normalized_sort_by == "compensation":
        columns = [func.coalesce(Job.compensation_min, 0), func.coalesce(Job.compensation_max, 0), Job.title]
    elif normalized_sort_by == "applicants":
        columns = [Job.applicant_count, Job.title]
    elif normalized_sort_by == "status":
        columns = [Job.status, Job.title]
    else:
        columns = [Job.created_at]

    tie_breaker = Job.id.desc() if normalized_sort_order == "descend" else Job.id.asc()
    return [direction(column) for column in columns] + [tie_breaker]


def ensure_job_editable(job: Job, current_admin: dict[str, Any] | None) -> None:
    if not can_edit_job(job, current_admin):
        raise ForbiddenException("Only the job owner can edit this job.")


def _is_assessment_reviewer_only(current_admin: dict[str, Any] | None) -> bool:
    if not current_admin:
        return False
    return is_assessment_reviewer_only_permissions(
        current_admin.get("permissions") or [],
        is_superuser=bool(current_admin.get("is_superuser")),
    )
