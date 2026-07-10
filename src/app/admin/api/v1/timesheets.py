from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.db.database import async_get_db
from ....modules.project_timesheet_record.schema import (
    ProjectTimesheetAnalyticsRead,
    ProjectTimesheetBatchCreateRequest,
    ProjectTimesheetBatchCreateResponse,
    ProjectTimesheetBatchDeleteRequest,
    ProjectTimesheetBatchDeleteResponse,
    ProjectTimesheetOverviewRead,
    ProjectTimesheetRecordRead,
    ProjectTimesheetUpdateRequest,
    ProjectTimesheetWorkspaceRead,
)
from ....modules.project_timesheet_record.service import (
    create_project_timesheet_records,
    delete_project_timesheet_records,
    list_project_timesheet_analytics,
    list_project_timesheet_overview,
    list_project_timesheet_workspace,
    update_project_timesheet_record,
)
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/timesheets", tags=["admin-timesheets"])


@router.get(
    "/overview",
    response_model=ProjectTimesheetOverviewRead,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def read_project_timesheet_overview(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    company_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    return await list_project_timesheet_overview(db=db, company_id=company_id)


@router.get(
    "/analytics",
    response_model=ProjectTimesheetAnalyticsRead,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def read_project_timesheet_analytics(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    company_id: int | None = Query(default=None, ge=1),
    project_id: int | None = Query(default=None, ge=1),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    language: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    role_name: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_project_timesheet_analytics(
        db=db,
        company_id=company_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        language=language,
        work_type=work_type,
        role_name=role_name,
        keyword=keyword,
    )


@router.get(
    "/companies/{company_id}/projects/{project_id}/workspace",
    response_model=ProjectTimesheetWorkspaceRead,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def read_project_timesheet_workspace(
    company_id: int,
    project_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    keyword: str | None = Query(default=None),
    advanced_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_project_timesheet_workspace(
        company_id=company_id,
        project_id=project_id,
        db=db,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        advanced_filter=advanced_filter,
    )


@router.post(
    "/companies/{company_id}/projects/{project_id}/records/batch",
    response_model=ProjectTimesheetBatchCreateResponse,
    status_code=201,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def create_project_timesheet_records_endpoint(
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchCreateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_project_timesheet_records(
        company_id=company_id,
        project_id=project_id,
        payload=payload,
        db=db,
        admin_user_id=int(current_admin["id"]),
    )


@router.post(
    "/companies/{company_id}/projects/{project_id}/records/batch-delete",
    response_model=ProjectTimesheetBatchDeleteResponse,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def delete_project_timesheet_records_endpoint(
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchDeleteRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await delete_project_timesheet_records(
        company_id=company_id,
        project_id=project_id,
        payload=payload,
        db=db,
        admin_user_id=int(current_admin["id"]),
    )


@router.patch(
    "/companies/{company_id}/projects/{project_id}/records/{record_id}",
    response_model=ProjectTimesheetRecordRead,
    dependencies=[Depends(require_admin_permission("工时记录"))],
)
async def update_project_timesheet_record_endpoint(
    company_id: int,
    project_id: int,
    record_id: int,
    payload: ProjectTimesheetUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_project_timesheet_record(
        company_id=company_id,
        project_id=project_id,
        record_id=record_id,
        payload=payload,
        db=db,
        admin_user_id=int(current_admin["id"]),
    )
