import pytest

from src.app.modules.job_progress.candidate_presentation import (
    build_candidate_presentation,
    summarize_candidate_presentations,
)

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.parametrize(
    (
        "current_stage",
        "assessment_enabled",
        "process_data",
        "contract_data",
        "expected",
    ),
    [
        (
            "pending_screening",
            True,
            {},
            None,
            ("under_review", "application_review", "view_details"),
        ),
        (
            "pending_screening",
            True,
            {"assessment_sent_at": "2026-07-10T08:00:00Z"},
            None,
            ("action_required", "assessment_file", "upload_assessment"),
        ),
        (
            "assessment_review",
            True,
            {"assessment_result": "需重新提交", "assessment_submissions": [{"asset_id": 101}]},
            None,
            ("action_required", "assessment_file", "upload_assessment"),
        ),
        (
            "assessment_review",
            True,
            {},
            None,
            ("under_review", "application_review", "view_details"),
        ),
        (
            "assessment_review",
            True,
            {"assessment_submitted_at": "2026-07-10T08:00:00Z"},
            None,
            ("under_review", "application_review", "view_details"),
        ),
        (
            "assessment_review",
            True,
            {"assessment_submitted_at": "2026-07-10T08:00:00Z", "assessment_submissions": [{"asset_id": 101}]},
            None,
            ("under_review", "assessment_file", "view_status"),
        ),
        (
            "assessment_review",
            True,
            {"assessment_submissions": [{"asset_id": 102, "submitted_at": "2026-07-10T08:00:00Z"}]},
            None,
            ("under_review", "assessment_file", "view_status"),
        ),
        (
            "screening_passed",
            True,
            {"salary_confirmed_at": "2026-07-10T08:00:00Z"},
            None,
            ("under_review", "rate_confirmation", "view_status"),
        ),
        (
            "screening_passed",
            True,
            {"onboarding_status": "已发砍价"},
            None,
            ("action_required", "rate_confirmation", "view_rate_instructions"),
        ),
        (
            "contract_pool",
            False,
            {},
            {"draft_contract_attachment": {"asset_id": 201}},
            ("action_required", "signed_contract", "upload_contract"),
        ),
        (
            "contract_pool",
            False,
            {},
            {
                "draft_contract_attachment": {"asset_id": 201},
                "candidate_signed_contract_attachment": {"asset_id": 202},
                "contract_review_status": "changes_requested",
            },
            ("action_required", "signed_contract", "upload_contract"),
        ),
        (
            "contract_pool",
            False,
            {},
            {
                "draft_contract_attachment": {"asset_id": 201},
                "candidate_signed_contract_attachment": {"asset_id": 202},
                "submitted_contract_at": "2026-07-10T08:00:00Z",
                "contract_review_status": "pending",
            },
            ("under_review", "signed_contract", "view_status"),
        ),
        (
            "active",
            False,
            {"onboarding_status": "成功签约"},
            {
                "candidate_signed_contract_attachment": {"asset_id": 202},
                "company_sealed_contract_attachment": {"asset_id": 203},
                "contract_review_status": "approved",
            },
            ("under_review", "task_group", "view_status"),
        ),
        (
            "active",
            False,
            {"onboarding_status": "已发大礼包", "gift_package_sent_at": "2026-07-10T08:00:00Z"},
            {
                "candidate_signed_contract_attachment": {"asset_id": 202},
                "company_sealed_contract_attachment": {"asset_id": 203},
            },
            ("action_required", "task_group", "view_joining_instructions"),
        ),
        (
            "active",
            False,
            {"onboarding_status": "已发大礼包", "onboarding_date": "2026-07-10"},
            {"company_sealed_contract_attachment": {"asset_id": 203}},
            ("onboarded", "onboarding_completed", "view_status"),
        ),
        (
            "rejected",
            True,
            {"rejected_from_stage": "pending_screening"},
            None,
            ("rejected", "application_review", "view_details"),
        ),
        (
            "rejected",
            True,
            {"rejected_from_stage": "assessment_review"},
            None,
            ("rejected", "assessment_file", "view_details"),
        ),
        (
            "rejected",
            False,
            {"rejected_from_stage": "screening_passed"},
            None,
            ("rejected", "rate_confirmation", "view_details"),
        ),
        (
            "rejected",
            False,
            {"rejected_from_stage": "contract_pool"},
            {"draft_contract_attachment": {"asset_id": 201}},
            ("rejected", "signed_contract", "view_details"),
        ),
        (
            "rejected",
            False,
            {"rejected_from_stage": "active", "onboarding_status": "已发大礼包"},
            {"company_sealed_contract_attachment": {"asset_id": 203}},
            ("rejected", "task_group", "view_details"),
        ),
        (
            "replaced",
            False,
            {"onboarding_status": "已发大礼包", "onboarding_date": "2026-07-01"},
            {"company_sealed_contract_attachment": {"asset_id": 203}},
            ("engagement_ended", "onboarding_completed", "view_details"),
        ),
        (
            "unexpected_stage",
            True,
            {"onboarding_status": "corrupt"},
            {"contract_review_status": "unknown"},
            ("under_review", "application_review", "view_details"),
        ),
    ],
)
def test_build_candidate_presentation_matrix(
    current_stage: str,
    assessment_enabled: bool,
    process_data: dict[str, object],
    contract_data: dict[str, object] | None,
    expected: tuple[str, str, str],
) -> None:
    presentation = build_candidate_presentation(
        current_stage=current_stage,
        assessment_enabled=assessment_enabled,
        process_data=process_data,
        contract_data=contract_data,
    )

    assert (
        presentation["candidate_status"],
        presentation["candidate_stage"],
        presentation["candidate_action"],
    ) == expected
    assert presentation["candidate_action_required"] is (expected[0] == "action_required")


@pytest.mark.parametrize(
    ("status", "status_label"),
    [
        ("under_review", "Under Review"),
        ("action_required", "Action Required"),
        ("rejected", "Rejected"),
        ("onboarded", "Successfully Onboarded"),
        ("engagement_ended", "Engagement Ended"),
    ],
)
def test_candidate_status_labels_are_candidate_facing(status: str, status_label: str) -> None:
    scenarios = {
        "under_review": ("pending_screening", {}, None),
        "action_required": ("pending_screening", {"assessment_sent_at": "2026-07-10"}, None),
        "rejected": ("rejected", {"rejected_from_stage": "pending_screening"}, None),
        "onboarded": ("active", {"onboarding_date": "2026-07-10"}, None),
        "engagement_ended": ("replaced", {"onboarding_date": "2026-07-10"}, None),
    }
    current_stage, process_data, contract_data = scenarios[status]

    presentation = build_candidate_presentation(
        current_stage=current_stage,
        assessment_enabled=True,
        process_data=process_data,
        contract_data=contract_data,
    )

    assert presentation["candidate_status_label"] == status_label


def test_candidate_action_copy_never_claims_an_unimplemented_mutation() -> None:
    rate = build_candidate_presentation(
        current_stage="screening_passed",
        assessment_enabled=False,
        process_data={"onboarding_status": "已发砍价"},
        contract_data=None,
    )
    joining = build_candidate_presentation(
        current_stage="active",
        assessment_enabled=False,
        process_data={"onboarding_status": "已发大礼包"},
        contract_data={"company_sealed_contract_attachment": {"asset_id": 203}},
    )

    assert rate["candidate_action_label"] == "View Instructions"
    assert joining["candidate_action_label"] == "View Instructions"
    assert "confirm" not in rate["candidate_action_label"].lower()
    assert "join" not in joining["candidate_action_label"].lower()


def test_submitted_states_explain_that_review_is_still_pending() -> None:
    assessment = build_candidate_presentation(
        current_stage="assessment_review",
        assessment_enabled=True,
        process_data={"assessment_submitted_at": "2026-07-10", "assessment_submissions": [{"asset_id": 101}]},
        contract_data=None,
    )
    contract = build_candidate_presentation(
        current_stage="contract_pool",
        assessment_enabled=False,
        process_data={},
        contract_data={
            "candidate_signed_contract_attachment": {"asset_id": 202},
            "contract_review_status": "pending",
        },
    )

    assert "submitted" in assessment["candidate_stage_body"].lower()
    assert "review" in assessment["candidate_stage_body"].lower()
    assert "submitted" in contract["candidate_stage_body"].lower()
    assert "review" in contract["candidate_stage_body"].lower()


def test_unknown_stage_with_stale_contract_data_falls_back_safely() -> None:
    result = build_candidate_presentation(
        current_stage="unexpected_stage",
        assessment_enabled=True,
        process_data={},
        contract_data={"draft_contract_attachment": {"asset_id": 1}},
    )

    assert (
        result["candidate_status"],
        result["candidate_stage"],
        result["candidate_action"],
    ) == ("under_review", "application_review", "view_details")


def test_assessment_revision_is_actionable_even_when_old_attachment_is_missing() -> None:
    result = build_candidate_presentation(
        current_stage="assessment_review",
        assessment_enabled=True,
        process_data={"assessment_result": "needs_revision"},
        contract_data=None,
    )

    assert result["candidate_stage"] == "assessment_file"
    assert result["candidate_action"] == "upload_assessment"


@pytest.mark.parametrize("stage", ["pending_screening", "active"])
def test_stale_contract_data_cannot_create_contract_action_in_unrelated_stage(stage: str) -> None:
    result = build_candidate_presentation(
        current_stage=stage,
        assessment_enabled=False,
        process_data={},
        contract_data={"draft_contract_attachment": {"asset_id": 1}},
    )

    assert result["candidate_stage"] == "application_review"
    assert result["candidate_action"] == "view_details"


def test_candidate_presentation_summary_uses_exclusive_buckets() -> None:
    presentations = [
        build_candidate_presentation(
            current_stage="contract_pool",
            assessment_enabled=False,
            process_data={},
            contract_data={"draft_contract_attachment": {"asset_id": 1}},
        ),
        build_candidate_presentation(
            current_stage="screening_passed",
            assessment_enabled=False,
            process_data={"onboarding_status": "已发砍价"},
            contract_data=None,
        ),
        build_candidate_presentation(
            current_stage="pending_screening",
            assessment_enabled=False,
            process_data={},
            contract_data=None,
        ),
    ]

    assert summarize_candidate_presentations(presentations) == {
        "contract_uploads": 1,
        "other_actions": 1,
        "monitoring": 1,
        "total_action_required": 2,
    }
