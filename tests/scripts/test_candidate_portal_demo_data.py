import pytest

from src.scripts import run_candidate_my_jobs_demo

pytestmark = pytest.mark.no_database_cleanup


EXPECTED_AUTO_CASES = {
    "application_review": {
        "status_label": "Under Review",
        "stage_title": "Application Review",
        "action_label": "View Details",
    },
    "assessment_action_required": {
        "status_label": "Action Required",
        "stage_title": "Assessment File",
        "action_label": "Upload Now",
    },
    "assessment_under_review": {
        "status_label": "Under Review",
        "stage_title": "Assessment File",
        "action_label": "View Details",
    },
    "rate_confirmation_waiting": {
        "status_label": "Under Review",
        "stage_title": "Rate Confirmation",
        "action_label": "View Details",
    },
    "rate_confirmation_action_required": {
        "status_label": "Action Required",
        "stage_title": "Rate Confirmation",
        "action_label": "Confirm Now",
    },
    "signed_contract_action_required": {
        "status_label": "Action Required",
        "stage_title": "Signed Contract",
        "action_label": "Upload Now",
    },
    "signed_contract_under_review": {
        "status_label": "Under Review",
        "stage_title": "Signed Contract",
        "action_label": "View Details",
    },
    "task_group_action_required": {
        "status_label": "Action Required",
        "stage_title": "Task Group",
        "action_label": "Join Now",
    },
    "successfully_onboarded": {
        "status_label": "Successfully Onboarded",
        "stage_title": "Onboarding Completed",
        "action_label": "View Details",
    },
    "rejected": {
        "status_label": "Rejected",
        "stage_title": "Application Review",
        "action_label": "View Details",
    },
}


def test_candidate_portal_demo_cases_cover_applications_status_matrix() -> None:
    cases = run_candidate_my_jobs_demo.build_expected_candidate_portal_cases()

    assert {case["key"]: case["expected_candidate_view"] for case in cases} == EXPECTED_AUTO_CASES


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
