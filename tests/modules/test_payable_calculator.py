from decimal import Decimal

import pytest

from src.app.modules.payable.calculator import (
    calculate_referral_milestones,
    calculate_salary,
    calculate_team_leader_pay,
)

pytestmark = pytest.mark.no_database_cleanup


def test_salary_uses_decimal_money_rounding() -> None:
    assert calculate_salary(work_hours=Decimal("3.335"), rate=Decimal("2.335")) == Decimal("7.82")
    assert calculate_salary(work_hours=Decimal("0"), rate=Decimal("5.00")) == Decimal("0.00")


def test_team_leader_pay_exposes_base_bonus_multiplier_and_total() -> None:
    result = calculate_team_leader_pay(
        base_pay=Decimal("418.88"),
        monthly_team_hours=Decimal("110.00"),
    )

    assert result.multiplier == Decimal("0.3")
    assert result.base_pay == Decimal("418.88")
    assert result.bonus == Decimal("33.00")
    assert result.amount == Decimal("451.88")


def test_referral_milestones_return_each_new_reward_without_exceeding_cap() -> None:
    milestones = [
        {"required_hours": "40.00", "reward_amount": "25.00"},
        {"required_hours": "100.00", "reward_amount": "50.00"},
        {"required_hours": "180.00", "reward_amount": "75.00"},
    ]

    reached = calculate_referral_milestones(
        work_hours=Decimal("120.00"),
        reward_cap=Decimal("60.00"),
        milestones=milestones,
    )

    assert [(item.milestone_index, item.required_hours, item.reward_amount) for item in reached] == [
        (0, Decimal("40.00"), Decimal("25.00")),
        (1, Decimal("100.00"), Decimal("35.00")),
    ]
    assert sum((item.reward_amount for item in reached), Decimal("0.00")) == Decimal("60.00")
