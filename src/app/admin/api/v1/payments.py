from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....application.payouts import reverse_payment
from ....core.db.database import async_get_db
from ....modules.payment.queries import list_payments
from ....modules.payment.schema import PaymentListPage, PaymentListQuery, PaymentRead, PaymentReverseRequest
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/payments", tags=["admin-payments"])
_permission = Depends(require_admin_permission("流水记录"))


@router.get("", response_model=PaymentListPage, dependencies=[_permission])
async def read_payments(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    _current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    keyword: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    user_id: int | None = Query(default=None, ge=1),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> PaymentListPage:
    return await list_payments(
        db=db,
        query=PaymentListQuery(
            page=page,
            page_size=page_size,
            keyword=keyword,
            payment_type=payment_type,
            user_id=user_id,
            month=month,
        ),
    )


@router.post("/{payment_id}/reverse", response_model=PaymentRead, dependencies=[_permission])
async def reverse(
    payment_id: int,
    payload: PaymentReverseRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> PaymentRead:
    return await reverse_payment(
        db=db,
        payment_id=payment_id,
        details=payload,
        admin_user_id=int(current_admin["id"]),
    )
