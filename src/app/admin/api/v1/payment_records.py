from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_admin_permission
from ....core.db.database import async_get_db
from ....modules.payment_record.schema import (
    PaymentRecordBatchCreateRequest,
    PaymentRecordBatchCreateResponse,
    PaymentRecordListPage,
    PaymentRecordOptionsRead,
)
from ....modules.payment_record.service import (
    create_payment_records_for_admin,
    get_payment_record_options_for_admin,
    list_payment_records_for_admin,
)


router = APIRouter(prefix="/payment-records", tags=["admin-payment-records"])


@router.get(
    "",
    response_model=PaymentRecordListPage,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def read_payment_records(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    user_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    return await list_payment_records_for_admin(
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        payment_type=payment_type,
        user_id=user_id,
    )


@router.get(
    "/options",
    response_model=PaymentRecordOptionsRead,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def read_payment_record_options(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await get_payment_record_options_for_admin(db=db)


@router.post(
    "/batch",
    response_model=PaymentRecordBatchCreateResponse,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def create_payment_record_batch(
    payload: PaymentRecordBatchCreateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_payment_records_for_admin(
        db=db,
        admin_user_id=int(current_admin["id"]),
        payload=payload,
    )
