from decimal import ROUND_HALF_UP, Decimal
from typing import Any

REFERRAL_BONUS_MODEL_STATUS_ACTIVE = "active"
REFERRAL_BONUS_MODEL_STATUS_INACTIVE = "inactive"
REFERRAL_BONUS_MODEL_STATUSES = {
    REFERRAL_BONUS_MODEL_STATUS_ACTIVE,
    REFERRAL_BONUS_MODEL_STATUS_INACTIVE,
}

DEFAULT_REFERRAL_BONUS_MODEL_NAME = "Default Referral Bonus"
DEFAULT_REFERRAL_BONUS_CURRENCY = "USD"
DEFAULT_REFERRAL_BONUS_MILESTONES: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("40.00"), Decimal("25.00")),
    (Decimal("100.00"), Decimal("50.00")),
    (Decimal("180.00"), Decimal("75.00")),
    (Decimal("300.00"), Decimal("150.00")),
)
DEFAULT_REFERRAL_BONUS_CAP = sum(
    (reward_amount for _, reward_amount in DEFAULT_REFERRAL_BONUS_MILESTONES),
    Decimal("0.00"),
)

TWO_DECIMALS = Decimal("0.01")


def quantize_bonus_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)


def normalize_referral_bonus_status(value: str | None) -> str:
    normalized = (value or REFERRAL_BONUS_MODEL_STATUS_ACTIVE).strip().lower()
    if normalized not in REFERRAL_BONUS_MODEL_STATUSES:
        raise ValueError("Unsupported referral bonus model status.")
    return normalized


def normalize_referral_bonus_currency(value: str | None) -> str:
    normalized = (value or DEFAULT_REFERRAL_BONUS_CURRENCY).strip().upper()
    if not normalized:
        return DEFAULT_REFERRAL_BONUS_CURRENCY
    return normalized[:8]


def default_referral_bonus_milestones_payload() -> list[dict[str, str]]:
    return [
        {
            "required_hours": str(required_hours),
            "reward_amount": str(reward_amount),
        }
        for required_hours, reward_amount in DEFAULT_REFERRAL_BONUS_MILESTONES
    ]


def normalize_referral_bonus_milestones(raw_items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    items = raw_items or []
    normalized: list[tuple[Decimal, Decimal]] = []
    seen_hours: set[Decimal] = set()
    for raw_item in items:
        required_hours = quantize_bonus_decimal(raw_item.get("required_hours"))
        reward_amount = quantize_bonus_decimal(raw_item.get("reward_amount"))
        if required_hours <= 0:
            raise ValueError("Milestone required hours must be greater than 0.")
        if reward_amount <= 0:
            raise ValueError("Milestone reward amount must be greater than 0.")
        if required_hours in seen_hours:
            raise ValueError("Milestone required hours must be unique.")
        seen_hours.add(required_hours)
        normalized.append((required_hours, reward_amount))

    normalized.sort(key=lambda item: item[0])
    return [
        {
            "required_hours": str(required_hours),
            "reward_amount": str(reward_amount),
        }
        for required_hours, reward_amount in normalized
    ]


def calculate_referral_bonus_reward(
    work_hours: Decimal | int | float | str | None,
    *,
    reward_cap: Decimal | int | float | str | None,
    milestones: list[dict[str, Any]] | None,
) -> Decimal:
    hours = quantize_bonus_decimal(work_hours)
    cap = quantize_bonus_decimal(reward_cap)
    reward = Decimal("0.00")
    for item in normalize_referral_bonus_milestones(milestones):
        if hours >= quantize_bonus_decimal(item.get("required_hours")):
            reward += quantize_bonus_decimal(item.get("reward_amount"))
    if cap > 0:
        reward = min(reward, cap)
    return quantize_bonus_decimal(reward)

