from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_admin_permission
from ....core.db.database import async_get_db
from ....modules.talent_profile.schema import (
    TalentProfileListPage,
    TalentProfileMergeRequest,
    TalentProfileRead,
)
from ....modules.talent_profile.service import (
    get_talent_profile,
    list_talent_profiles,
    merge_application_into_talent,
)

router = APIRouter(prefix="/talents", tags=["admin-talents"])


@router.get("", response_model=TalentProfileListPage, dependencies=[Depends(require_admin_permission("总人才库"))])
async def read_talents(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
) -> dict[str, Any]:
    return await list_talent_profiles(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
    )


@router.get("/{talent_id}", response_model=TalentProfileRead, dependencies=[Depends(require_admin_permission("总人才库"))])
async def read_talent(
    talent_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await get_talent_profile(talent_id, db)


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
