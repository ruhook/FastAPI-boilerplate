from enum import StrEnum


class PayableStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    CANCELLED = "cancelled"
    REVERSED = "reversed"
