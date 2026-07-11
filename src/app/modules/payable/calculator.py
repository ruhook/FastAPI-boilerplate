from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_TWO_DECIMALS = Decimal("0.01")
_ONE_HOUR = Decimal("1")


def _decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP)


def calculate_salary(
    *,
    work_hours: Decimal | int | float | str | None,
    rate: Decimal | int | float | str | None,
) -> Decimal:
    return (_decimal(work_hours) * _decimal(rate)).quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP)


def _team_leader_multiplier(monthly_team_hours: Decimal) -> Decimal:
    rounded_hours = monthly_team_hours.quantize(_ONE_HOUR, rounding=ROUND_HALF_UP)
    if rounded_hours < Decimal("800"):
        return Decimal("0.3")
    if rounded_hours < Decimal("1200"):
        return Decimal("0.4")
    if rounded_hours < Decimal("2000"):
        return Decimal("0.5")
    if rounded_hours < Decimal("3000"):
        return Decimal("0.6")
    return Decimal("0.8")


@dataclass(frozen=True, slots=True)
class TeamLeaderPayCalculation:
    amount: Decimal
    base_pay: Decimal
    bonus: Decimal
    multiplier: Decimal


def calculate_team_leader_pay(
    *,
    base_pay: Decimal | int | float | str | None,
    monthly_team_hours: Decimal | int | float | str | None,
) -> TeamLeaderPayCalculation:
    hours = _decimal(monthly_team_hours)
    normalized_base_pay = _decimal(base_pay)
    multiplier = _team_leader_multiplier(hours)
    bonus = (hours * multiplier).quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP)
    return TeamLeaderPayCalculation(
        amount=(normalized_base_pay + bonus).quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP),
        base_pay=normalized_base_pay,
        bonus=bonus,
        multiplier=multiplier,
    )


@dataclass(frozen=True, slots=True)
class ReferralMilestoneCalculation:
    milestone_index: int
    required_hours: Decimal
    reward_amount: Decimal


def calculate_referral_milestones(
    *,
    work_hours: Decimal | int | float | str | None,
    reward_cap: Decimal | int | float | str | None,
    milestones: list[dict[str, Any]],
) -> list[ReferralMilestoneCalculation]:
    hours = _decimal(work_hours)
    cap = _decimal(reward_cap)
    remaining = cap if cap > 0 else None
    reached: list[ReferralMilestoneCalculation] = []
    for index, milestone in enumerate(milestones):
        required_hours = _decimal(milestone.get("required_hours"))
        reward_amount = _decimal(milestone.get("reward_amount"))
        if required_hours <= 0 or reward_amount <= 0:
            raise ValueError("Referral milestones require positive hours and rewards.")
        if hours < required_hours:
            continue
        if remaining is not None:
            reward_amount = min(reward_amount, remaining)
            remaining -= reward_amount
        if reward_amount > 0:
            reached.append(
                ReferralMilestoneCalculation(
                    milestone_index=index,
                    required_hours=required_hours,
                    reward_amount=reward_amount,
                )
            )
        if remaining is not None and remaining <= 0:
            break
    return reached
