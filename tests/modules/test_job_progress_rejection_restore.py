from datetime import date

import pytest

from src.app.modules.job_progress.const import RecruitmentStage, get_allowed_recruitment_stage_transitions
from src.app.modules.job_progress.rejection_restore import (
    build_rejected_progress_data,
    pop_active_contract_restore_data,
)

pytestmark = pytest.mark.no_database_cleanup


def test_rejected_stage_can_restore_to_every_supported_source_stage() -> None:
    assert set(
        get_allowed_recruitment_stage_transitions(
            RecruitmentStage.REJECTED.value,
            assessment_enabled=True,
        )
    ) == {
        RecruitmentStage.PENDING_SCREENING,
        RecruitmentStage.ASSESSMENT_REVIEW,
        RecruitmentStage.SCREENING_PASSED,
        RecruitmentStage.CONTRACT_POOL,
        RecruitmentStage.ACTIVE,
    }


def test_active_rejection_preserves_contract_state_without_mutating_source_data() -> None:
    source_data = {"onboarding_status": "已发大礼包"}

    rejected_data = build_rejected_progress_data(
        source_data,
        source_stage=RecruitmentStage.ACTIVE.value,
        contract_status="Active",
        contract_end_date=date(2026, 12, 31),
    )

    assert source_data == {"onboarding_status": "已发大礼包"}
    assert rejected_data["rejected_from_stage"] == "active"
    assert rejected_data["rejected_contract_previous_status"] == "Active"
    assert rejected_data["rejected_contract_previous_end_date"] == "2026-12-31"


def test_active_restore_returns_contract_values_and_clears_temporary_metadata() -> None:
    cleaned_data, status, end_date = pop_active_contract_restore_data(
        {
            "onboarding_status": "已发大礼包",
            "rejected_from_stage": "active",
            "rejected_contract_previous_status": "Active",
            "rejected_contract_previous_end_date": "2026-12-31",
        }
    )

    assert cleaned_data == {"onboarding_status": "已发大礼包"}
    assert status == "Active"
    assert end_date == date(2026, 12, 31)


def test_active_restore_preserves_an_empty_previous_end_date() -> None:
    cleaned_data, status, end_date = pop_active_contract_restore_data(
        {
            "rejected_from_stage": "active",
            "rejected_contract_previous_status": "Active",
            "rejected_contract_previous_end_date": None,
        }
    )

    assert cleaned_data == {}
    assert status == "Active"
    assert end_date is None


def test_non_active_rejection_removes_stale_contract_restore_metadata() -> None:
    rejected_data = build_rejected_progress_data(
        {
            "rejected_contract_previous_status": "Stale",
            "rejected_contract_previous_end_date": "2025-01-01",
        },
        source_stage=RecruitmentStage.CONTRACT_POOL.value,
    )

    assert rejected_data == {"rejected_from_stage": "contract_pool"}
