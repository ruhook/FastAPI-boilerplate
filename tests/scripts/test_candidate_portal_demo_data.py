from types import SimpleNamespace

import pytest

from src.scripts import run_candidate_my_jobs_demo

pytestmark = pytest.mark.no_database_cleanup


EXPECTED_AUTO_CASES = {
    "application_review": ("under_review", "application_review", "view_details", False),
    "assessment_action_required": ("action_required", "assessment_file", "upload_assessment", True),
    "assessment_revision_required": ("action_required", "assessment_file", "upload_assessment", True),
    "assessment_under_review": ("under_review", "assessment_file", "view_status", False),
    "rate_confirmation_waiting": ("under_review", "rate_confirmation", "view_status", False),
    "rate_confirmation_action_required": (
        "action_required",
        "rate_confirmation",
        "view_rate_instructions",
        True,
    ),
    "signed_contract_action_required": ("action_required", "signed_contract", "upload_contract", True),
    "signed_contract_revision_required": ("action_required", "signed_contract", "upload_contract", True),
    "signed_contract_under_review": ("under_review", "signed_contract", "view_status", False),
    "onboarding_preparation": ("under_review", "task_group", "view_status", False),
    "task_group_action_required": (
        "action_required",
        "task_group",
        "view_joining_instructions",
        True,
    ),
    "successfully_onboarded": ("onboarded", "onboarding_completed", "view_status", False),
    "rejected": ("rejected", "application_review", "view_details", False),
    "rejected_late_stage": ("rejected", "signed_contract", "view_details", False),
    "engagement_ended": ("engagement_ended", "onboarding_completed", "view_details", False),
}


def test_candidate_portal_demo_cases_cover_applications_status_matrix() -> None:
    cases = run_candidate_my_jobs_demo.build_expected_candidate_portal_cases()

    assert {
        case["key"]: (
            case["expected_candidate_view"]["candidate_status"],
            case["expected_candidate_view"]["candidate_stage"],
            case["expected_candidate_view"]["candidate_action"],
            case["expected_candidate_view"]["candidate_action_required"],
        )
        for case in cases
    } == EXPECTED_AUTO_CASES


def test_candidate_portal_demo_has_more_than_one_applications_page() -> None:
    cases = run_candidate_my_jobs_demo.build_expected_candidate_portal_cases()

    assert len(cases) == 15
    assert len(cases) > 10


def test_candidate_portal_demo_expected_summary_and_action_set_are_exact() -> None:
    cases = run_candidate_my_jobs_demo.build_expected_candidate_portal_cases()

    assert run_candidate_my_jobs_demo.build_expected_candidate_summary(cases) == {
        "contract_uploads": 2,
        "other_actions": 4,
        "monitoring": 9,
        "total_action_required": 6,
    }
    assert run_candidate_my_jobs_demo.get_expected_action_required_case_keys(cases) == {
        "assessment_action_required",
        "assessment_revision_required",
        "rate_confirmation_action_required",
        "signed_contract_action_required",
        "signed_contract_revision_required",
        "task_group_action_required",
    }


def test_candidate_portal_demo_has_one_manual_fresh_apply_job() -> None:
    manual_definitions = [
        definition
        for definition in run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS
        if not run_candidate_my_jobs_demo.should_auto_apply(definition)
    ]

    assert [definition["key"] for definition in manual_definitions] == ["fresh_apply_flow"]


def test_candidate_portal_demo_jobs_use_resettable_title_prefix() -> None:
    prefix = run_candidate_my_jobs_demo.CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX

    assert prefix == "Candidate Portal Demo - "
    assert all(
        definition["title"].startswith(prefix)
        for definition in run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS
    )


def test_candidate_portal_demo_current_title_guard_excludes_obsolete_titles() -> None:
    assert run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job_title(
        "Candidate Portal Demo - Assessment Under Review"
    )
    assert not run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job_title(
        "Candidate Portal Demo - Assessment Review"
    )


def test_candidate_portal_demo_does_not_require_auto_assessment_mail_task() -> None:
    assert run_candidate_my_jobs_demo.should_verify_auto_assessment_mail_task() is False


def test_demo_mail_task_scope_requires_recipient_and_demo_reference() -> None:
    demo_task = SimpleNamespace(
        to_recipients=[{"email": "712696307@qq.com"}],
        data={
            "render_context": {
                "job": {"title": "Candidate Portal Demo - Rejected"},
                "job_progress": {"id": 301},
            }
        },
    )
    unrelated_task = SimpleNamespace(
        to_recipients=[{"email": "712696307@qq.com"}],
        data={"render_context": {"job": {"title": "A real non-demo role"}}},
    )
    wrong_recipient_task = SimpleNamespace(
        to_recipients=[{"email": "someone@example.com"}],
        data={"render_context": {"job": {"title": "Candidate Portal Demo - Rejected"}}},
    )

    scope = {
        "candidate_email": "712696307@qq.com",
        "job_titles": {"Candidate Portal Demo - Rejected"},
        "application_ids": {201},
        "progress_ids": {301},
    }
    assert run_candidate_my_jobs_demo.mail_task_targets_demo_scope(demo_task, **scope) is True
    assert run_candidate_my_jobs_demo.mail_task_targets_demo_scope(unrelated_task, **scope) is False
    assert run_candidate_my_jobs_demo.mail_task_targets_demo_scope(wrong_recipient_task, **scope) is False
