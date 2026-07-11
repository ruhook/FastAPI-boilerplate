import pytest

from src.app.modules.job_progress.const import RecruitmentStage, get_allowed_recruitment_stage_transitions
from src.app.modules.job_progress.rejection_restore import build_rejected_progress_data

pytestmark = pytest.mark.no_database_cleanup


def test_rejected_stage_cannot_restore_a_terminated_active_contract() -> None:
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
    }


def test_rejection_stores_only_recruitment_source_stage() -> None:
    source_data = {"onboarding_status": "gift_sent"}

    rejected_data = build_rejected_progress_data(
        source_data,
        source_stage=RecruitmentStage.ACTIVE.value,
    )

    assert source_data == {"onboarding_status": "gift_sent"}
    assert rejected_data == {
        "onboarding_status": "gift_sent",
        "rejected_from_stage": "active",
    }
