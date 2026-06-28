from decimal import ROUND_HALF_UP, Decimal

PAYMENT_TYPE_SALARY = "salary"
PAYMENT_TYPE_TEAM_LEADER_BONUS = "team_leader_bonus"
PAYMENT_TYPE_REFERRAL_REWARD = "referral_reward"

PAYMENT_PAYOUT_STATUS_PENDING = "pending"
PAYMENT_PAYOUT_STATUS_PAID = "paid"
PAYMENT_PAYOUT_STATUS_RETURNED = "returned"
PAYMENT_PAYOUT_STATUSES = {
    PAYMENT_PAYOUT_STATUS_PENDING,
    PAYMENT_PAYOUT_STATUS_PAID,
    PAYMENT_PAYOUT_STATUS_RETURNED,
}

PAYMENT_SOURCE_AUTO_PAYABLE = "auto_calculated_payable"

PAYMENT_TYPE_LABELS = {
    PAYMENT_TYPE_SALARY: "生产薪资",
    PAYMENT_TYPE_TEAM_LEADER_BONUS: "组长薪资",
    PAYMENT_TYPE_REFERRAL_REWARD: "内推奖金",
}

PAYMENT_TYPES = set(PAYMENT_TYPE_LABELS)


def normalize_payment_type(value: str | None) -> str:
    text = (value or "").strip()
    if text not in PAYMENT_TYPES:
        raise ValueError("Invalid payment type.")
    return text


def normalize_payment_payout_status(value: str | None) -> str:
    text = (value or "").strip()
    if text not in PAYMENT_PAYOUT_STATUSES:
        raise ValueError("Invalid payout status.")
    return text


def quantize_money(value: Decimal | int | float | str | None) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
