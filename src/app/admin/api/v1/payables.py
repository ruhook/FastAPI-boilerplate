from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....application.payouts import pay_payables
from ....core.db.database import async_get_db
from ....modules.payable.commands import create_manual_payable, transition_payables
from ....modules.payable.const import PayableStatus
from ....modules.payable.queries import list_payables
from ....modules.payable.schema import (
    ManualPayableCreateRequest,
    PayableBatchResponse,
    PayableIdsRequest,
    PayableListPage,
    PayableListQuery,
    PayableRead,
)
from ....modules.payment.schema import BatchPayoutRequest, BatchPayoutResult
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/payables", tags=["admin-payables"])
_permission = Depends(require_admin_permission("流水记录"))


@router.get("", response_model=PayableListPage, dependencies=[_permission])
async def read_payables(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    settlement_month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    payment_type: str | None = Query(default=None),
    status: PayableStatus | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> PayableListPage:
    return await list_payables(
        db=db,
        query=PayableListQuery(
            page=page,
            page_size=page_size,
            settlement_month=settlement_month,
            payment_type=payment_type,
            status=status,
            keyword=keyword,
        ),
    )


@router.post("/manual", response_model=PayableRead, dependencies=[_permission])
async def create_manual(
    payload: ManualPayableCreateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> PayableRead:
    payable = await create_manual_payable(
        db=db,
        payload=payload,
        admin_user_id=int(current_admin["id"]),
    )
    return PayableRead.model_validate(payable)


async def _transition(
    *,
    db: AsyncSession,
    payload: PayableIdsRequest,
    current_admin: dict[str, Any],
    target: PayableStatus,
) -> PayableBatchResponse:
    items = await transition_payables(
        db=db,
        payable_ids=payload.payable_ids,
        target=target,
        admin_user_id=int(current_admin["id"]),
    )
    return PayableBatchResponse(items=[PayableRead.model_validate(item) for item in items])


@router.post("/processing", response_model=PayableBatchResponse, dependencies=[_permission])
async def start_processing(
    payload: PayableIdsRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> PayableBatchResponse:
    return await _transition(db=db, payload=payload, current_admin=current_admin, target=PayableStatus.PROCESSING)


@router.post("/reopen", response_model=PayableBatchResponse, dependencies=[_permission])
async def reopen(
    payload: PayableIdsRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> PayableBatchResponse:
    return await _transition(db=db, payload=payload, current_admin=current_admin, target=PayableStatus.PENDING)


@router.post("/cancel", response_model=PayableBatchResponse, dependencies=[_permission])
async def cancel(
    payload: PayableIdsRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> PayableBatchResponse:
    return await _transition(db=db, payload=payload, current_admin=current_admin, target=PayableStatus.CANCELLED)


@router.post("/pay", response_model=BatchPayoutResult, dependencies=[_permission])
async def pay(
    payload: BatchPayoutRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> BatchPayoutResult:
    return await pay_payables(
        db=db,
        payable_ids=payload.payable_ids,
        details=payload.payout_details(),
        admin_user_id=int(current_admin["id"]),
    )
