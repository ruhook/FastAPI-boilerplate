from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_superuser, require_any_admin_permission
from .....core.db.database import async_get_db
from .....modules.referral_bonus_model.schema import (
    ReferralBonusModelCreate,
    ReferralBonusModelRead,
    ReferralBonusModelUpdate,
)
from .....modules.referral_bonus_model.service import (
    create_referral_bonus_model,
    delete_referral_bonus_model,
    get_referral_bonus_model_model,
    list_referral_bonus_models,
    serialize_referral_bonus_model,
    update_referral_bonus_model,
)

router = APIRouter(prefix="/referral-bonus-models", tags=["admin-referral-bonus-models"])

REFERRAL_BONUS_MODEL_READ_PERMISSIONS = ("岗位管理", "邀请奖励", "流水记录")


@router.get(
    "",
    response_model=list[ReferralBonusModelRead],
    dependencies=[Depends(require_any_admin_permission(*REFERRAL_BONUS_MODEL_READ_PERMISSIONS))],
)
async def read_referral_bonus_models(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    include_inactive: bool = Query(default=True),
) -> list[dict[str, Any]]:
    return await list_referral_bonus_models(db=db, include_inactive=include_inactive)


@router.get(
    "/{model_id}",
    response_model=ReferralBonusModelRead,
    dependencies=[Depends(require_any_admin_permission(*REFERRAL_BONUS_MODEL_READ_PERMISSIONS))],
)
async def read_referral_bonus_model(
    model_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    model = await get_referral_bonus_model_model(model_id, db)
    return serialize_referral_bonus_model(model)


@router.post(
    "",
    response_model=ReferralBonusModelRead,
    status_code=201,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def create_referral_bonus_model_endpoint(
    payload: ReferralBonusModelCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await create_referral_bonus_model(payload, db, admin_user_id=int(current_admin["id"]))


@router.patch(
    "/{model_id}",
    response_model=ReferralBonusModelRead,
    dependencies=[Depends(get_current_admin_superuser)],
)
async def update_referral_bonus_model_endpoint(
    model_id: int,
    payload: ReferralBonusModelUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, Any]:
    return await update_referral_bonus_model(model_id, payload, db, admin_user_id=int(current_admin["id"]))


@router.delete(
    "/{model_id}",
    dependencies=[Depends(get_current_admin_superuser)],
)
async def delete_referral_bonus_model_endpoint(
    model_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_superuser)],
) -> dict[str, str]:
    return await delete_referral_bonus_model(model_id, db, admin_user_id=int(current_admin["id"]))

