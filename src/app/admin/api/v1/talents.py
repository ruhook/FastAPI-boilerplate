from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.db.database import async_get_db
from ....modules.talent_profile.commands import (
    join_talent_to_job,
    update_talent_pool_note,
    update_talent_pool_status,
)
from ....modules.talent_profile.merge import merge_application_into_talent
from ....modules.talent_profile.queries import (
    get_talent_profile,
    get_talent_profile_by_user_id,
    list_talent_profiles,
)
from ....modules.talent_profile.schema import (
    TalentJoinJobRequest,
    TalentJoinJobResponse,
    TalentNoteUpdateRequest,
    TalentProfileListPage,
    TalentProfileMergeRequest,
    TalentProfileRead,
    TalentStatusUpdateRequest,
)
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/talents", tags=["admin-talents"])


@router.post(
    "/{talent_id}/join-job",
    response_model=TalentJoinJobResponse,
    dependencies=[Depends(require_admin_permission("总人才库"))],
)
async def join_talent_to_job_endpoint(
    talent_id: int,
    payload: TalentJoinJobRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await join_talent_to_job(
        talent_id=talent_id,
        job_id=payload.job_id,
        current_admin=current_admin,
        db=db,
    )


@router.get("", response_model=TalentProfileListPage, dependencies=[Depends(require_admin_permission("总人才库"))])
async def read_talents(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    company_id: int | None = Query(default=None, ge=1),
    project_id: int | None = Query(default=None, ge=1),
    advanced_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_talent_profiles(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        company_id=company_id,
        project_id=project_id,
        advanced_filter=advanced_filter,
    )


@router.get(
    "/by-user/{user_id}",
    response_model=TalentProfileRead,
    dependencies=[Depends(require_admin_permission("总人才库"))],
)
async def read_talent_by_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await get_talent_profile_by_user_id(user_id, db)


@router.get(
    "/{talent_id}", response_model=TalentProfileRead, dependencies=[Depends(require_admin_permission("总人才库"))]
)
async def read_talent(
    talent_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await get_talent_profile(talent_id, db)


@router.patch(
    "/{talent_id}/note",
    response_model=TalentProfileRead,
    dependencies=[Depends(require_admin_permission("总人才库"))],
)
async def update_talent_note_endpoint(
    talent_id: int,
    payload: TalentNoteUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_talent_pool_note(
        talent_id=talent_id,
        note=payload.note,
        current_admin=current_admin,
        db=db,
    )


@router.patch(
    "/{talent_id}/status",
    response_model=TalentProfileRead,
    dependencies=[Depends(require_admin_permission("总人才库"))],
)
async def update_talent_status_endpoint(
    talent_id: int,
    payload: TalentStatusUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_talent_pool_status(
        talent_id=talent_id,
        status=payload.status,
        current_admin=current_admin,
        db=db,
    )


@router.post(
    "/{talent_id}/merge-from-application/{application_id}",
    response_model=TalentProfileRead,
    dependencies=[Depends(require_admin_permission("总人才库"))],
)
async def merge_application_endpoint(
    talent_id: int,
    application_id: int,
    payload: TalentProfileMergeRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await merge_application_into_talent(
        talent_id=talent_id,
        application_id=application_id,
        current_admin=current_admin,
        db=db,
        fields=payload.fields or None,
    )
