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

EXPECTED_DEMO_TITLES = {
    "fresh_apply_flow": "待申请",
    "application_review": "申请审核中",
    "assessment_action_required": "待上传测试题",
    "assessment_under_review": "测试题审核中",
    "rate_confirmation_waiting": "费率确认待通知",
    "rate_confirmation_action_required": "待查看费率说明",
    "signed_contract_action_required": "待上传签署合同",
    "signed_contract_under_review": "合同审核中",
    "task_group_action_required": "待查看入组说明",
    "successfully_onboarded": "已成功入职",
    "rejected": "已拒绝（申请审核阶段）",
    "assessment_revision_required": "测试题待重新提交",
    "signed_contract_revision_required": "合同待重新提交",
    "onboarding_preparation": "入职准备中",
    "rejected_late_stage": "已拒绝（合同阶段）",
    "engagement_ended": "合作已结束",
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


def test_candidate_portal_demo_jobs_use_exact_chinese_state_copy() -> None:
    definitions = run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS

    assert {item["key"]: item["title"] for item in definitions} == EXPECTED_DEMO_TITLES
    assert len({item["title"] for item in definitions}) == 16
    assert all("C端验收" not in item["title"] for item in definitions)
    assert all("葡语数据标注员" not in item["title"] for item in definitions)
    assert {
        item["description"] for item in definitions
    } == {run_candidate_my_jobs_demo.CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION}


def test_candidate_portal_demo_current_title_guard_excludes_obsolete_titles() -> None:
    assert run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job_title(
        "Candidate Portal Demo - Assessment Under Review"
    )
    assert not run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job_title(
        "Candidate Portal Demo - Assessment Review"
    )


def test_candidate_portal_demo_does_not_require_auto_assessment_mail_task() -> None:
    assert run_candidate_my_jobs_demo.should_verify_auto_assessment_mail_task() is False


def test_rate_waiting_demo_uses_a_real_candidate_assessment_upload() -> None:
    definition = next(
        item
        for item in run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS
        if item["key"] == "rate_confirmation_waiting"
    )

    assert definition["assessment_submission_file_name"] == "rate-confirmation-waiting.xlsx"


def test_rate_waiting_demo_validation_rejects_missing_assessment_attachment() -> None:
    definition = next(
        item
        for item in run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS
        if item["key"] == "rate_confirmation_waiting"
    )
    item = {
        "current_stage": definition["target_stage"],
        **run_candidate_my_jobs_demo.EXPECTED_CANDIDATE_VIEW_BY_KEY["rate_confirmation_waiting"],
        "process_data": {"assessment_result": "通过"},
        "contract_record_data": {},
    }

    with pytest.raises(RuntimeError, match="missing its uploaded file"):
        run_candidate_my_jobs_demo.assert_candidate_demo_item_matches_definition(item, definition)


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
