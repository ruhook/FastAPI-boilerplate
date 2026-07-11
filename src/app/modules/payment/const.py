from enum import StrEnum


class PaymentEntryType(StrEnum):
    PAYMENT = "payment"
    REVERSAL = "reversal"
