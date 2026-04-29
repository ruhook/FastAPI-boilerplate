from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_admin_permission
from ....core.db.database import async_get_db
from ....modules.referral.schema import AdminReferralListPage, AdminReferralMarkPaidResponse
from ....modules.referral.service import list_referrals_for_admin, mark_referral_reward_paid


router = APIRouter(prefix="/referrals", tags=["admin-referrals"])


@router.get(
    "",
    response_model=AdminReferralListPage,
    dependencies=[Depends(require_admin_permission("邀请奖励"))],
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


@router.post(
    "/{referral_record_id}/mark-paid",
    response_model=AdminReferralMarkPaidResponse,
    dependencies=[Depends(require_admin_permission("邀请奖励"))],
)
async def mark_referral_paid(
    referral_record_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    item = await mark_referral_reward_paid(
        referral_record_id=referral_record_id,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )
    return AdminReferralMarkPaidResponse(item=item).model_dump()

