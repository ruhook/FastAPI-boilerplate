from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import (
    get_current_admin_superuser,
    require_any_admin_permission,
)
from .....core.db.database import async_get_db
from .....modules.admin.company.schema import (
    CompanyCreate,
    CompanyProjectCreate,
    CompanyProjectMenuCompanyRead,
    CompanyProjectRead,
    CompanyProjectUpdate,
    CompanyRead,
    CompanyUpdate,
)
from .....modules.admin.company.service import (
    create_company,
    create_company_project,
    delete_company,
    delete_company_project,
    get_company_model,
    get_company_project_model,
    list_companies,
    list_company_project_menu,
    list_company_projects,
    serialize_company,
    serialize_company_project,
    update_company,
    update_company_project,
)

router = APIRouter(prefix="/companies", tags=["admin-companies"])

COMPANY_PROJECT_READ_PERMISSIONS = ("公司管理", "岗位管理", "合同管理", "工时记录", "总人才库")


@router.get(
    "",
    response_model=list[CompanyRead],
    dependencies=[Depends(require_any_admin_permission(*COMPANY_PROJECT_READ_PERMISSIONS))],
)
async def read_companies(db: Annotated[AsyncSession, Depends(async_get_db)]) -> list[dict[str, Any]]:
    return await list_companies(db)


@router.get(
    "/project-menu",
    response_model=list[CompanyProjectMenuCompanyRead],
    dependencies=[Depends(require_any_admin_permission(*COMPANY_PROJECT_READ_PERMISSIONS))],
)
async def read_company_project_menu(db: Annotated[AsyncSession, Depends(async_get_db)]) -> list[dict[str, Any]]:
    return await list_company_project_menu(db)


@router.get(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[Depends(require_any_admin_permission(*COMPANY_PROJECT_READ_PERMISSIONS))],
)
async def read_company(
    company_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    company = await get_company_model(company_id, db)
    return await serialize_company(company, db)


@router.post(
    "",
    response_model=CompanyRead,
    status_code=201,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def create_company_endpoint(
    payload: CompanyCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await create_company(payload, db, admin_user_id=int(current_admin["id"]))


@router.patch(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def update_company_endpoint(
    company_id: int,
    payload: CompanyUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await update_company(company_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete(
    "/{company_id}",
    dependencies=[Depends(get_current_admin_superuser)],
)
async def delete_company_endpoint(
    company_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, str]:
    return await delete_company(company_id, db, admin_user_id=int(current_admin["id"]))


@router.get(
    "/{company_id}/projects",
    response_model=list[CompanyProjectRead],
    dependencies=[Depends(require_any_admin_permission(*COMPANY_PROJECT_READ_PERMISSIONS))],
)
async def read_company_projects(
    company_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> list[dict[str, Any]]:
    return await list_company_projects(company_id, db)


@router.get(
    "/{company_id}/projects/{project_id}",
    response_model=CompanyProjectRead,
    dependencies=[Depends(require_any_admin_permission(*COMPANY_PROJECT_READ_PERMISSIONS))],
)
async def read_company_project(
    company_id: int,
    project_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    project = await get_company_project_model(company_id, project_id, db)
    return serialize_company_project(project)


@router.post(
    "/{company_id}/projects",
    response_model=CompanyProjectRead,
    status_code=201,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def create_company_project_endpoint(
    company_id: int,
    payload: CompanyProjectCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await create_company_project(company_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.patch(
    "/{company_id}/projects/{project_id}",
    response_model=CompanyProjectRead,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def update_company_project_endpoint(
    company_id: int,
    project_id: int,
    payload: CompanyProjectUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await update_company_project(
        company_id,
        project_id,
        payload,
        db,
        admin_user_id=int(current_admin["id"]),
    )


@router.delete(
    "/{company_id}/projects/{project_id}",
    dependencies=[Depends(get_current_admin_superuser)],
)
async def delete_company_project_endpoint(
    company_id: int,
    project_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, str]:
    return await delete_company_project(company_id, project_id, db, admin_user_id=int(current_admin["id"]))
