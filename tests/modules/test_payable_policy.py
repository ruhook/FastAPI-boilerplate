from uuid import UUID

import pytest

from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.policy import ensure_payable_transition
from src.app.modules.payable.source_keys import (
    manual_source_key,
    referral_reward_source_key,
    salary_source_key,
    team_leader_bonus_source_key,
)

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PayableStatus.PENDING, PayableStatus.PROCESSING),
        (PayableStatus.PENDING, PayableStatus.CANCELLED),
        (PayableStatus.PROCESSING, PayableStatus.PENDING),
        (PayableStatus.PROCESSING, PayableStatus.PAID),
        (PayableStatus.PROCESSING, PayableStatus.CANCELLED),
        (PayableStatus.PAID, PayableStatus.REVERSED),
    ],
)
def test_allowed_payable_transitions(current: PayableStatus, target: PayableStatus) -> None:
    ensure_payable_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PayableStatus.PENDING, PayableStatus.PAID),
        (PayableStatus.PAID, PayableStatus.PROCESSING),
        (PayableStatus.CANCELLED, PayableStatus.PENDING),
        (PayableStatus.REVERSED, PayableStatus.PENDING),
        (PayableStatus.PENDING, PayableStatus.PENDING),
    ],
)
def test_disallowed_payable_transitions_raise_conflict(
    current: PayableStatus,
    target: PayableStatus,
) -> None:
    with pytest.raises(ConflictException, match="transition is not allowed"):
        ensure_payable_transition(current, target)


def test_source_keys_use_stable_business_dimensions() -> None:
    assert salary_source_key(month="2026-07", user_id=3, contract_record_id=9) == "salary:2026-07:3:9"
    assert team_leader_bonus_source_key(month="2026-07", user_id=3, project_id=12) == (
        "team_leader_bonus:2026-07:3:12"
    )
    assert referral_reward_source_key(referral_record_id=8, milestone_index=2) == "referral_reward:8:2"


def test_manual_source_key_uses_a_unique_uuid() -> None:
    first = manual_source_key()
    second = manual_source_key()

    assert first.startswith("manual:")
    assert second.startswith("manual:")
    assert first != second
    UUID(first.removeprefix("manual:"))
