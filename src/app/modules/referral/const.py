from decimal import Decimal, ROUND_HALF_UP


REFERRAL_REWARD_MILESTONES: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("40.00"), Decimal("25.00")),
    (Decimal("100.00"), Decimal("50.00")),
    (Decimal("180.00"), Decimal("75.00")),
    (Decimal("300.00"), Decimal("150.00")),
)
REFERRAL_REWARD_CAP = sum((reward_amount for _, reward_amount in REFERRAL_REWARD_MILESTONES), Decimal("0.00"))

REFERRAL_STATUS_TRACKING = "tracking"
REFERRAL_STATUS_READY_TO_PAY = "ready_to_pay"
REFERRAL_STATUS_PAID = "paid"

TWO_DECIMALS = Decimal("0.01")


def quantize_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)


def calculate_referral_reward(work_hours: Decimal | int | float | str | None) -> Decimal:
    hours = quantize_decimal(work_hours)
    reward = Decimal("0.00")
    for required_hours, reward_amount in REFERRAL_REWARD_MILESTONES:
        if hours >= required_hours:
            reward += reward_amount
    return quantize_decimal(reward)
