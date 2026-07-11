import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import NotFoundException
from ..admin.dictionary.service import get_dictionary_option_label_map_by_key
from ..candidate_field.service import hydrate_candidate_field_options
from .const import (
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_SHOW_COMPENSATION_KEY,
    JobStatus,
)
from .model import Job


class WebJobListItemRead(BaseModel):
    id: int
    title: str
    company: str
    status: str
    country: str
    country_label: str | None = None
    work_mode: str
    show_compensation: bool = True
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
    company: str
    status: str
    country: str
    country_label: str | None = None
    work_mode: str
    show_compensation: bool = True
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str
    compensation_label: str
    description_html: str
    contract_example_html: str = ""
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


def _should_show_compensation(job: Job) -> bool:
    return bool((job.data or {}).get(JOB_DATA_SHOW_COMPENSATION_KEY, True))


def _resolve_country_label(country: str, country_label_map: dict[str, str]) -> str | None:
    normalized = country.strip()
    if not normalized:
        return None
    return country_label_map.get(normalized)


def _serialize_job_list_item(
    job: Job,
    *,
    country_label_map: dict[str, str],
) -> dict[str, Any]:
    return WebJobListItemRead(
        id=job.id,
        title=job.title,
        company="",
        status=job.status,
        country=job.country,
        country_label=_resolve_country_label(job.country, country_label_map),
        work_mode=job.work_mode,
        show_compensation=_should_show_compensation(job),
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        compensation_label=_build_compensation_label(job) if _should_show_compensation(job) else "-",
        summary=_build_summary(job),
        published_at=job.created_at,
    ).model_dump()


async def _serialize_job_detail(
    job: Job,
    *,
    country_label_map: dict[str, str],
    db: AsyncSession,
) -> dict[str, Any]:
    data = job.data or {}
    form_fields = await hydrate_candidate_field_options(
        list(data.get(JOB_DATA_FORM_FIELDS_KEY) or []),
        db=db,
    )
    return WebJobDetailRead(
        id=job.id,
        title=job.title,
        company="",
        status=job.status,
        country=job.country,
        country_label=_resolve_country_label(job.country, country_label_map),
        work_mode=job.work_mode,
        show_compensation=_should_show_compensation(job),
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        compensation_label=_build_compensation_label(job) if _should_show_compensation(job) else "-",
        description_html=job.description,
        contract_example_html=str(data.get(JOB_DATA_CONTRACT_EXAMPLE_KEY) or ""),
        summary=_build_summary(job),
        process=_build_process(job),
        form_template_id=job.form_template_id,
        form_fields=form_fields,
        published_at=job.created_at,
        assessment_enabled=job.assessment_enabled,
    ).model_dump()

async def list_public_jobs(
    *,
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    work_mode: str | None = None,
    country: str | None = None,
) -> WebJobListPage:
    conditions: list[Any] = [Job.is_deleted.is_(False), Job.status == JobStatus.OPEN.value]
    country_label_map = await get_dictionary_option_label_map_by_key(key="country", db=db)
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
    return WebJobListPage(
        items=[
            WebJobListItemRead.model_validate(_serialize_job_list_item(job, country_label_map=country_label_map))
            for job in result.scalars().all()
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_public_job(*, job_id: int, db: AsyncSession) -> WebJobDetailRead:
    job = (
        await db.scalars(
            select(Job).where(
                Job.id == job_id,
                Job.is_deleted.is_(False),
                Job.status == JobStatus.OPEN.value,
            )
        )
    ).one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    country_label_map = await get_dictionary_option_label_map_by_key(key="country", db=db)
    return WebJobDetailRead.model_validate(
        await _serialize_job_detail(job, country_label_map=country_label_map, db=db)
    )

