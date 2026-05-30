from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.db.database import async_get_db
from ....modules.payment_record.schema import (
    PaymentPayableListPage,
    PaymentPayableMarkPaidRequest,
    PaymentPayableMarkPaidResponse,
    PaymentPayableUpdateRequest,
    PaymentPayableUpdateResponse,
    PaymentRecordBatchCreateRequest,
    PaymentRecordBatchCreateResponse,
    PaymentRecordListPage,
    PaymentRecordOptionsRead,
)
from ....modules.payment_record.service import (
    create_payment_records_for_admin,
    get_payment_record_options_for_admin,
    list_auto_payment_payables_for_admin,
    list_payment_records_for_admin,
    mark_auto_payment_payables_paid,
    update_auto_payment_payable_info,
)
from ..dependencies import get_current_admin_user, require_admin_permission

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
    "/payables",
    response_model=PaymentPayableListPage,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def read_payment_payables(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    keyword: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    payout_status: str | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_order: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_auto_payment_payables_for_admin(
        db=db,
        page=page,
        page_size=page_size,
        month=month,
        keyword=keyword,
        payment_type=payment_type,
        payout_status=payout_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.post(
    "/payables/mark-paid",
    response_model=PaymentPayableMarkPaidResponse,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def mark_payment_payables_paid(
    payload: PaymentPayableMarkPaidRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await mark_auto_payment_payables_paid(
        db=db,
        admin_user_id=int(current_admin["id"]),
        payload=payload,
    )


@router.patch(
    "/payables/{payment_record_id}",
    response_model=PaymentPayableUpdateResponse,
    dependencies=[Depends(require_admin_permission("流水记录"))],
)
async def update_payment_payable_info(
    payment_record_id: int,
    payload: PaymentPayableUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_auto_payment_payable_info(
        db=db,
        admin_user_id=int(current_admin["id"]),
        payment_record_id=payment_record_id,
        payload=payload,
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
