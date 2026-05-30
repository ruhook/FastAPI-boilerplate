from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import BadRequestException, DuplicateValueException, NotFoundException
from ...assets.service import ensure_assets_exist, serialize_asset
from ..admin_audit_log.const import AdminAuditLogActionType, AdminAuditLogTargetType
from ..admin_audit_log.service import create_admin_audit_log
from ...contract_record.model import ContractRecord
from ...job.model import Job
from .model import AdminCompany, AdminCompanyProject
from .schema import (
    CompanyCreate,
    CompanyProjectCreate,
    CompanyProjectMenuCompanyRead,
    CompanyProjectMenuProjectRead,
    CompanyProjectRead,
    CompanyProjectUpdate,
    CompanyRead,
    CompanyUpdate,
)

COMPANY_DATA_TIMESHEET_LANGUAGES_KEY = "timesheet_languages"
COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY = "timesheet_work_types"
COMPANY_DATA_TIMESHEET_ROLES_KEY = "timesheet_roles"


def _serialize_timesheet_languages(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_LANGUAGES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _serialize_timesheet_work_types(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _serialize_timesheet_roles(company: AdminCompany) -> list[str]:
    value = (company.data or {}).get(COMPANY_DATA_TIMESHEET_ROLES_KEY)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


async def _load_logo_asset(company: AdminCompany, db: AsyncSession) -> dict[str, Any] | None:
    if company.logo_asset_id is None:
        return None
    assets = await ensure_assets_exist(db, asset_ids=[company.logo_asset_id])
    if not assets:
        return None
    return serialize_asset(assets[0])


async def serialize_company(company: AdminCompany, db: AsyncSession) -> dict[str, Any]:
    logo_asset = await _load_logo_asset(company, db)
    return CompanyRead(
        id=company.id,
        name=company.name,
        description=company.description,
        logo_asset_id=company.logo_asset_id,
        logo_asset=logo_asset,
        timesheet_languages=_serialize_timesheet_languages(company),
        timesheet_work_types=_serialize_timesheet_work_types(company),
        timesheet_roles=_serialize_timesheet_roles(company),
        created_at=company.created_at,
        updated_at=company.updated_at,
        data=company.data or {},
    ).model_dump()


def serialize_company_project(project: AdminCompanyProject) -> dict[str, Any]:
    return CompanyProjectRead(
        id=project.id,
        company_id=project.company_id,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
        data=project.data or {},
    ).model_dump()


async def list_companies(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AdminCompany)
        .where(AdminCompany.is_deleted.is_(False))
        .order_by(AdminCompany.name.asc(), AdminCompany.id.asc())
    )
    companies = result.scalars().all()
    return [await serialize_company(company, db) for company in companies]


async def list_company_project_menu(db: AsyncSession) -> list[dict[str, Any]]:
    companies_result = await db.execute(
        select(AdminCompany.id, AdminCompany.name)
        .where(AdminCompany.is_deleted.is_(False))
        .order_by(AdminCompany.name.asc(), AdminCompany.id.asc())
    )
    companies = companies_result.all()

    projects_result = await db.execute(
        select(AdminCompanyProject.id, AdminCompanyProject.company_id, AdminCompanyProject.name)
        .where(AdminCompanyProject.is_deleted.is_(False))
        .order_by(AdminCompanyProject.company_id.asc(), AdminCompanyProject.name.asc(), AdminCompanyProject.id.asc())
    )
    projects_by_company: dict[int, list[CompanyProjectMenuProjectRead]] = defaultdict(list)
    for project_id, company_id, project_name in projects_result.all():
        projects_by_company[int(company_id)].append(
            CompanyProjectMenuProjectRead(id=project_id, company_id=company_id, name=project_name)
        )

    return [
        CompanyProjectMenuCompanyRead(
            id=company_id,
            name=company_name,
            projects=projects_by_company.get(int(company_id), []),
        ).model_dump()
        for company_id, company_name in companies
    ]


async def get_company_model(company_id: int, db: AsyncSession) -> AdminCompany:
    result = await db.execute(
        select(AdminCompany).where(
            AdminCompany.id == company_id,
            AdminCompany.is_deleted.is_(False),
        )
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise NotFoundException("Company not found.")
    return company


async def list_company_projects(company_id: int, db: AsyncSession) -> list[dict[str, Any]]:
    await get_company_model(company_id, db)
    result = await db.execute(
        select(AdminCompanyProject)
        .where(
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.is_deleted.is_(False),
        )
        .order_by(AdminCompanyProject.name.asc(), AdminCompanyProject.id.asc())
    )
    return [serialize_company_project(project) for project in result.scalars().all()]


async def get_company_project_model(company_id: int, project_id: int, db: AsyncSession) -> AdminCompanyProject:
    result = await db.execute(
        select(AdminCompanyProject).where(
            AdminCompanyProject.id == project_id,
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundException("Project not found.")
    return project


async def _validate_company_logo(logo_asset_id: int | None, db: AsyncSession) -> None:
    if logo_asset_id is None:
        return
    await ensure_assets_exist(db, asset_ids=[logo_asset_id])


def _raise_company_integrity_error(exc: IntegrityError) -> None:
    raise DuplicateValueException("Company name already exists.") from exc


def _raise_company_project_integrity_error(exc: IntegrityError) -> None:
    raise DuplicateValueException("Project name already exists in this company.") from exc


async def create_company(payload: CompanyCreate, db: AsyncSession, *, admin_user_id: int) -> dict[str, Any]:
    existing = await db.execute(
        select(AdminCompany).where(
            AdminCompany.name == payload.name,
            AdminCompany.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Company name already exists.")

    await _validate_company_logo(payload.logo_asset_id, db)

    company = AdminCompany(
        name=payload.name,
        description=payload.description,
        logo_asset_id=payload.logo_asset_id,
        data={
            COMPANY_DATA_TIMESHEET_LANGUAGES_KEY: list(payload.timesheet_languages),
            COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY: list(payload.timesheet_work_types),
            COMPANY_DATA_TIMESHEET_ROLES_KEY: list(payload.timesheet_roles),
        },
    )
    db.add(company)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_company_integrity_error(exc)

    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_CREATED.value,
        target_type=AdminAuditLogTargetType.COMPANY.value,
        target_id=company.id,
        data={"name": company.name},
    )
    await db.refresh(company)
    return await serialize_company(company, db)


async def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    company = await get_company_model(company_id, db)

    if payload.name and payload.name != company.name:
        existing = await db.execute(
            select(AdminCompany).where(
                AdminCompany.name == payload.name,
                AdminCompany.is_deleted.is_(False),
                AdminCompany.id != company_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Company name already exists.")
        company.name = payload.name

    if "description" in payload.model_fields_set:
        company.description = payload.description

    if "logo_asset_id" in payload.model_fields_set:
        await _validate_company_logo(payload.logo_asset_id, db)
        company.logo_asset_id = payload.logo_asset_id

    next_data = dict(company.data or {})
    if "timesheet_languages" in payload.model_fields_set:
        next_data[COMPANY_DATA_TIMESHEET_LANGUAGES_KEY] = list(payload.timesheet_languages or [])
    if "timesheet_work_types" in payload.model_fields_set:
        next_data[COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY] = list(payload.timesheet_work_types or [])
    if "timesheet_roles" in payload.model_fields_set:
        next_data[COMPANY_DATA_TIMESHEET_ROLES_KEY] = list(payload.timesheet_roles or [])
    company.data = next_data

    company.updated_at = datetime.now(UTC)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_company_integrity_error(exc)

    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_UPDATED.value,
        target_type=AdminAuditLogTargetType.COMPANY.value,
        target_id=company.id,
        data={"name": company.name},
    )
    await db.refresh(company)
    return await serialize_company(company, db)


async def create_company_project(
    company_id: int,
    payload: CompanyProjectCreate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    await get_company_model(company_id, db)
    existing = await db.execute(
        select(AdminCompanyProject).where(
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.name == payload.name,
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateValueException("Project name already exists in this company.")

    project = AdminCompanyProject(company_id=company_id, name=payload.name, data={})
    db.add(project)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_company_project_integrity_error(exc)

    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_PROJECT_CREATED.value,
        target_type=AdminAuditLogTargetType.COMPANY_PROJECT.value,
        target_id=project.id,
        data={"company_id": company_id, "name": project.name},
    )
    await db.refresh(project)
    return serialize_company_project(project)


async def update_company_project(
    company_id: int,
    project_id: int,
    payload: CompanyProjectUpdate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    project = await get_company_project_model(company_id, project_id, db)

    if payload.name is not None and payload.name != project.name:
        existing = await db.execute(
            select(AdminCompanyProject).where(
                AdminCompanyProject.company_id == company_id,
                AdminCompanyProject.name == payload.name,
                AdminCompanyProject.id != project_id,
                AdminCompanyProject.is_deleted.is_(False),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateValueException("Project name already exists in this company.")
        project.name = payload.name

    project.updated_at = datetime.now(UTC)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_company_project_integrity_error(exc)

    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_PROJECT_UPDATED.value,
        target_type=AdminAuditLogTargetType.COMPANY_PROJECT.value,
        target_id=project.id,
        data={"company_id": company_id, "name": project.name},
    )
    await db.refresh(project)
    return serialize_company_project(project)


async def delete_company_project(
    company_id: int,
    project_id: int,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, str]:
    project = await get_company_project_model(company_id, project_id, db)

    job_result = await db.execute(
        select(Job.id).where(
            Job.project_id == project_id,
            Job.is_deleted.is_(False),
        ).limit(1)
    )
    if job_result.scalar_one_or_none() is not None:
        raise BadRequestException("Project is still used by jobs.")

    contract_result = await db.execute(
        select(ContractRecord.id).where(
            ContractRecord.service_customer_project_id == project_id,
            ContractRecord.is_deleted.is_(False),
        ).limit(1)
    )
    if contract_result.scalar_one_or_none() is not None:
        raise BadRequestException("Project is still used by contracts.")

    project.is_deleted = True
    project.deleted_at = datetime.now(UTC)
    project.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_PROJECT_DELETED.value,
        target_type=AdminAuditLogTargetType.COMPANY_PROJECT.value,
        target_id=project.id,
        data={"company_id": company_id, "name": project.name},
    )
    return {"message": "Project deleted."}


async def delete_company(company_id: int, db: AsyncSession, *, admin_user_id: int) -> dict[str, str]:
    company = await get_company_model(company_id, db)
    project_result = await db.execute(
        select(AdminCompanyProject.id).where(
            AdminCompanyProject.company_id == company_id,
            AdminCompanyProject.is_deleted.is_(False),
        ).limit(1)
    )
    if project_result.scalar_one_or_none() is not None:
        raise BadRequestException("Company is still used by projects.")
    job_result = await db.execute(
        select(Job.id).where(
            Job.company_id == company_id,
            Job.is_deleted.is_(False),
        ).limit(1)
    )
    if job_result.scalar_one_or_none() is not None:
        raise BadRequestException("Company is still used by jobs.")

    company.is_deleted = True
    company.deleted_at = datetime.now(UTC)
    company.updated_at = datetime.now(UTC)
    await db.flush()
    await create_admin_audit_log(
        db=db,
        admin_user_id=admin_user_id,
        action_type=AdminAuditLogActionType.COMPANY_DELETED.value,
        target_type=AdminAuditLogTargetType.COMPANY.value,
        target_id=company.id,
        data={"name": company.name},
    )
    return {"message": "Company deleted."}
