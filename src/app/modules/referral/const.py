from decimal import ROUND_HALF_UP, Decimal

REFERRAL_STATUS_TRACKING = "tracking"
REFERRAL_STATUS_READY_TO_PAY = "ready_to_pay"
REFERRAL_STATUS_PAID = "paid"

TWO_DECIMALS = Decimal("0.01")


def quantize_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
