from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions.http_exceptions import ConflictException, NotFoundException
from ..modules.operation_log.const import OperationLogType
from ..modules.operation_log.service import create_operation_log
from ..modules.payment.commands import pay_payable, reverse_paid_payment
from ..modules.payment.schema import (
    BatchPayoutItemResult,
    BatchPayoutResult,
    PaymentRead,
    PayoutDetails,
)


async def pay_payables(
    *,
    db: AsyncSession,
    payable_ids: list[int],
    details: PayoutDetails,
    admin_user_id: int | None,
) -> BatchPayoutResult:
    items: list[BatchPayoutItemResult] = []
    for payable_id in sorted(set(payable_ids)):
        try:
            async with db.begin_nested():
                payment, created = await pay_payable(
                    db=db,
                    payable_id=payable_id,
                    details=details,
                    admin_user_id=admin_user_id,
                )
                if created:
                    await create_operation_log(
                        db=db,
                        user_id=payment.user_id,
                        talent_profile_id=payment.talent_profile_id,
                        log_type=OperationLogType.PAYABLES_PAID.value,
                        data={
                            "payable_id": payable_id,
                            "payment_id": payment.id,
                            "operator_admin_user_id": admin_user_id,
                        },
                    )
                items.append(
                    BatchPayoutItemResult(
                        payable_id=payable_id,
                        payment=PaymentRead.model_validate(payment),
                    )
                )
        except (ConflictException, NotFoundException) as exc:
            items.append(BatchPayoutItemResult(payable_id=payable_id, error=str(exc.detail)))

    failed_count = sum(item.error is not None for item in items)
    return BatchPayoutResult(
        items=items,
        paid_count=len(items) - failed_count,
        failed_count=failed_count,
    )


async def reverse_payment(
    *,
    db: AsyncSession,
    payment_id: int,
    details: PayoutDetails,
    admin_user_id: int | None,
) -> PaymentRead:
    payment, created = await reverse_paid_payment(
        db=db,
        payment_id=payment_id,
        details=details,
        admin_user_id=admin_user_id,
    )
    if created:
        await create_operation_log(
            db=db,
            user_id=payment.user_id,
            talent_profile_id=payment.talent_profile_id,
            log_type=OperationLogType.PAYMENT_REVERSED.value,
            data={
                "payable_id": payment.payable_id,
                "payment_id": payment.id,
                "reversal_of_payment_id": payment.reversal_of_payment_id,
                "operator_admin_user_id": admin_user_id,
            },
        )
    return PaymentRead.model_validate(payment)
