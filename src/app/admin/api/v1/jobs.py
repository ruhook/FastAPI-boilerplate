from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_admin_permission
from ....core.db.database import async_get_db
from ....modules.admin.job.schema import JobCreate, JobListPage, JobRead, JobUpdate
from ....modules.admin.job.service import create_job, get_job, list_jobs, update_job

router = APIRouter(prefix="/jobs", tags=["admin-jobs"])


@router.get("", response_model=JobListPage, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def read_jobs(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    status: str | None = None,
    company: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    return await list_jobs(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
        company=company,
        country=country,
    )


@router.post("", response_model=JobRead, status_code=201, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def create_job_endpoint(
    payload: JobCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_job(payload, db, current_admin=current_admin)


@router.get("/{job_id}", response_model=JobRead, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def read_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await get_job(job_id, db)


@router.patch("/{job_id}", response_model=JobRead, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def update_job_endpoint(
    job_id: int,
    payload: JobUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_job(job_id, payload, db, current_admin=current_admin)

