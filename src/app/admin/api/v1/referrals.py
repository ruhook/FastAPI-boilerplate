from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.db.database import async_get_db
from ....modules.referral.schema import AdminReferralListPage
from ....modules.referral.service import list_referrals_for_admin
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/referrals", tags=["admin-referrals"])


@router.get(
    "",
    response_model=AdminReferralListPage,
    dependencies=[Depends(require_admin_permission("内推奖金"))],
)
async def read_referrals(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    payout_status: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_referrals_for_admin(
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        payout_status=payout_status,
    )
