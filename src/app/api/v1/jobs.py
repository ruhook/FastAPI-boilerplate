import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import NotFoundException
from ...modules.admin.job.const import (
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_STATUS_OPEN,
)
from ...modules.admin.job.model import Job

router = APIRouter(prefix="/jobs", tags=["web-jobs"])


class WebJobListItemRead(BaseModel):
    id: int
    title: str
    status: str
    country: str
    work_mode: str
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str
    compensation_label: str
    summary: str
    published_at: datetime


class WebJobListPage(BaseModel):
    items: list[WebJobListItemRead]
    total: int
    page: int
    page_size: int


class WebJobDetailRead(BaseModel):
    id: int
    title: str
    status: str
    country: str
    work_mode: str
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str
    compensation_label: str
    description_html: str
    summary: str
    process: list[str] = Field(default_factory=list)
    form_template_id: int
    form_fields: list[dict[str, Any]] = Field(default_factory=list)
    published_at: datetime
    assessment_enabled: bool


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", text).strip()


def _build_summary(job: Job) -> str:
    return _strip_html(job.description)[:220] or job.title


def _build_compensation_label(job: Job) -> str:
    if job.compensation_min is None and job.compensation_max is None:
        return "-"
    min_value = float(job.compensation_min or 0)
    max_value = float(job.compensation_max or job.compensation_min or 0)
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".")
    max_text = f"{max_value:.2f}".rstrip("0").rstrip(".")
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


def _build_process(job: Job) -> list[str]:
    base = ["Create account", "Submit application"]
    if job.assessment_enabled:
        base.append("Complete assessment")
    base.append("Hiring team review")
    return base


def _serialize_job_list_item(job: Job) -> dict[str, Any]:
    return WebJobListItemRead(
        id=job.id,
        title=job.title,
        status=job.status,
        country=job.country,
        work_mode=job.work_mode,
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        compensation_label=_build_compensation_label(job),
        summary=_build_summary(job),
        published_at=job.created_at,
    ).model_dump()


def _serialize_job_detail(job: Job) -> dict[str, Any]:
    data = job.data or {}
    return WebJobDetailRead(
        id=job.id,
        title=job.title,
        status=job.status,
        country=job.country,
        work_mode=job.work_mode,
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        compensation_label=_build_compensation_label(job),
        description_html=job.description,
        summary=_build_summary(job),
        process=_build_process(job),
        form_template_id=job.form_template_id,
        form_fields=list(data.get(JOB_DATA_FORM_FIELDS_KEY) or []),
        published_at=job.created_at,
        assessment_enabled=job.assessment_enabled,
    ).model_dump()


@router.get("", response_model=WebJobListPage)
async def list_public_jobs(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    work_mode: str | None = Query(default=None),
    country: str | None = Query(default=None),
) -> dict[str, Any]:
    conditions = [Job.is_deleted.is_(False), Job.status == JOB_STATUS_OPEN]

    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(or_(Job.title.ilike(term), Job.country.ilike(term)))
    if work_mode:
        conditions.append(Job.work_mode == work_mode)
    if country:
        conditions.append(Job.country == country)

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
    return WebJobListPage(
        items=[_serialize_job_list_item(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()


@router.get("/{job_id}", response_model=WebJobDetailRead)
async def get_public_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
            Job.status == JOB_STATUS_OPEN,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    return _serialize_job_detail(job)
