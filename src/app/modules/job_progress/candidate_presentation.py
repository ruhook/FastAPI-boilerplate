from collections.abc import Mapping
from typing import Any, Literal, TypedDict

CandidateStatus = Literal[
    "under_review",
    "action_required",
    "rejected",
    "onboarded",
    "engagement_ended",
]
CandidateStage = Literal[
    "application_review",
    "assessment_file",
    "rate_confirmation",
    "signed_contract",
    "task_group",
    "onboarding_completed",
]
CandidateAction = Literal[
    "view_details",
    "view_status",
    "upload_assessment",
    "view_rate_instructions",
    "upload_contract",
    "view_joining_instructions",
]


class CandidatePresentation(TypedDict):
    candidate_status: CandidateStatus
    candidate_stage: CandidateStage
    candidate_action: CandidateAction
    candidate_action_required: bool
    candidate_status_label: str
    candidate_stage_title: str
    candidate_stage_body: str
    candidate_action_label: str


class CandidatePresentationSummary(TypedDict):
    contract_uploads: int
    other_actions: int
    monitoring: int
    total_action_required: int


STATUS_LABELS: dict[CandidateStatus, str] = {
    "under_review": "Under Review",
    "action_required": "Action Required",
    "rejected": "Rejected",
    "onboarded": "Successfully Onboarded",
    "engagement_ended": "Engagement Ended",
}

STAGE_TITLES: dict[CandidateStage, str] = {
    "application_review": "Application Review",
    "assessment_file": "Assessment File",
    "rate_confirmation": "Rate Confirmation",
    "signed_contract": "Signed Contract",
    "task_group": "Task Group",
    "onboarding_completed": "Onboarding Completed",
}

ACTION_LABELS: dict[CandidateAction, str] = {
    "view_details": "View Details",
    "view_status": "View Status",
    "upload_assessment": "Upload Assessment",
    "view_rate_instructions": "View Instructions",
    "upload_contract": "Upload Contract",
    "view_joining_instructions": "View Instructions",
}

APPLICATION_REVIEW_BODY = "Your application is under review. We will notify you when the next step is ready."
ASSESSMENT_UPLOAD_BODY = "Upload your completed assessment file to continue."
ASSESSMENT_REVIEW_BODY = "Your assessment has been submitted and is awaiting review."
RATE_WAITING_BODY = "You have passed the review stage. We will notify you when the next step is ready."
RATE_INSTRUCTIONS_BODY = (
    "Please check your email for the proposed rate and reply there to confirm how you wish to proceed."
)
CONTRACT_UPLOAD_BODY = "Please download, sign, and upload your signed contract."
CONTRACT_REVIEW_BODY = "Your signed contract has been submitted and is awaiting review."
TASK_GROUP_WAITING_BODY = "Your contract is complete. We will notify you when the onboarding instructions are ready."
TASK_GROUP_INSTRUCTIONS_BODY = "Please follow the onboarding email instructions to complete the final step."
ONBOARDED_BODY = "You have completed the onboarding process and officially joined the team."
REJECTED_BODY = "This application has been closed at this stage."
ENGAGEMENT_ENDED_BODY = "This engagement has ended and is no longer active."

SUPPORTED_PROGRESS_STAGES = {
    "pending_screening",
    "assessment_review",
    "screening_passed",
    "contract_pool",
    "active",
    "rejected",
    "replaced",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    return bool(_text(value))


def _has_contract_asset(contract_data: Mapping[str, Any], key: str) -> bool:
    return _has_value(contract_data.get(key))


def _has_draft_contract(contract_data: Mapping[str, Any]) -> bool:
    return _has_contract_asset(contract_data, "draft_contract_attachment")


def _has_candidate_signed_contract(contract_data: Mapping[str, Any]) -> bool:
    return _has_contract_asset(contract_data, "candidate_signed_contract_attachment")


def _has_company_sealed_contract(contract_data: Mapping[str, Any]) -> bool:
    return _has_contract_asset(contract_data, "company_sealed_contract_attachment") or _has_contract_asset(
        contract_data, "contract_attachment"
    )


def _has_assessment_submission(process_data: Mapping[str, Any]) -> bool:
    submissions = process_data.get("assessment_submissions")
    if not isinstance(submissions, list):
        return False
    return any(
        isinstance(item, Mapping) and _text(item.get("asset_id")).lower() not in {"", "0", "none", "null"}
        for item in submissions
    )


def _needs_assessment_revision(process_data: Mapping[str, Any]) -> bool:
    result = _text(process_data.get("assessment_result")).lower()
    return result in {"需重新提交", "needs revision", "needs_revision", "resubmit", "re-submit"}


def _needs_contract_revision(contract_data: Mapping[str, Any]) -> bool:
    return _text(contract_data.get("contract_review_status")).lower() == "changes_requested"


def _rejected_stage(process_data: Mapping[str, Any]) -> CandidateStage:
    source_stage = _text(process_data.get("rejected_from_stage")).lower()
    if source_stage == "assessment_review":
        return "assessment_file"
    if source_stage == "screening_passed":
        return "rate_confirmation"
    if source_stage == "contract_pool":
        return "signed_contract"
    if source_stage == "active":
        if _has_value(process_data.get("onboarding_date")):
            return "onboarding_completed"
        return "task_group"
    return "application_review"


def _build(
    *,
    status: CandidateStatus,
    stage: CandidateStage,
    action: CandidateAction,
    body: str,
) -> CandidatePresentation:
    return {
        "candidate_status": status,
        "candidate_stage": stage,
        "candidate_action": action,
        "candidate_action_required": status == "action_required",
        "candidate_status_label": STATUS_LABELS[status],
        "candidate_stage_title": STAGE_TITLES[stage],
        "candidate_stage_body": body,
        "candidate_action_label": ACTION_LABELS[action],
    }


def build_candidate_presentation(
    *,
    current_stage: str,
    assessment_enabled: bool,
    process_data: Mapping[str, Any] | None,
    contract_data: Mapping[str, Any] | None,
) -> CandidatePresentation:
    process = process_data or {}
    contract = contract_data or {}
    normalized_stage = _text(current_stage).lower()

    if normalized_stage not in SUPPORTED_PROGRESS_STAGES:
        return _build(
            status="under_review",
            stage="application_review",
            action="view_details",
            body=APPLICATION_REVIEW_BODY,
        )

    if normalized_stage == "replaced":
        final_stage: CandidateStage = (
            "onboarding_completed" if _has_value(process.get("onboarding_date")) else "task_group"
        )
        return _build(
            status="engagement_ended",
            stage=final_stage,
            action="view_details",
            body=ENGAGEMENT_ENDED_BODY,
        )

    if normalized_stage == "rejected":
        return _build(
            status="rejected",
            stage=_rejected_stage(process),
            action="view_details",
            body=REJECTED_BODY,
        )

    if normalized_stage == "active" and _has_value(process.get("onboarding_date")):
        return _build(
            status="onboarded",
            stage="onboarding_completed",
            action="view_status",
            body=ONBOARDED_BODY,
        )

    if normalized_stage == "active" and _text(process.get("onboarding_status")) == "已发大礼包":
        return _build(
            status="action_required",
            stage="task_group",
            action="view_joining_instructions",
            body=TASK_GROUP_INSTRUCTIONS_BODY,
        )

    if normalized_stage == "active" and _has_company_sealed_contract(contract):
        return _build(
            status="under_review",
            stage="task_group",
            action="view_status",
            body=TASK_GROUP_WAITING_BODY,
        )

    if normalized_stage in {"screening_passed", "contract_pool"} and (
        _has_draft_contract(contract) or _has_candidate_signed_contract(contract)
    ):
        if not _has_candidate_signed_contract(contract) or _needs_contract_revision(contract):
            return _build(
                status="action_required",
                stage="signed_contract",
                action="upload_contract",
                body=CONTRACT_UPLOAD_BODY,
            )
        return _build(
            status="under_review",
            stage="signed_contract",
            action="view_status",
            body=CONTRACT_REVIEW_BODY,
        )

    if normalized_stage == "assessment_review":
        if _needs_assessment_revision(process):
            return _build(
                status="action_required",
                stage="assessment_file",
                action="upload_assessment",
                body=ASSESSMENT_UPLOAD_BODY,
            )
        if _has_assessment_submission(process):
            return _build(
                status="under_review",
                stage="assessment_file",
                action="view_status",
                body=ASSESSMENT_REVIEW_BODY,
            )

    if normalized_stage == "pending_screening" and assessment_enabled and _has_value(process.get("assessment_sent_at")):
        return _build(
            status="action_required",
            stage="assessment_file",
            action="upload_assessment",
            body=ASSESSMENT_UPLOAD_BODY,
        )

    if normalized_stage == "screening_passed":
        if _text(process.get("onboarding_status")) == "已发砍价":
            return _build(
                status="action_required",
                stage="rate_confirmation",
                action="view_rate_instructions",
                body=RATE_INSTRUCTIONS_BODY,
            )
        return _build(
            status="under_review",
            stage="rate_confirmation",
            action="view_status",
            body=RATE_WAITING_BODY,
        )

    return _build(
        status="under_review",
        stage="application_review",
        action="view_details",
        body=APPLICATION_REVIEW_BODY,
    )


def summarize_candidate_presentations(
    presentations: list[CandidatePresentation],
) -> CandidatePresentationSummary:
    contract_uploads = sum(presentation["candidate_action"] == "upload_contract" for presentation in presentations)
    other_actions = sum(
        presentation["candidate_action_required"] and presentation["candidate_action"] != "upload_contract"
        for presentation in presentations
    )
    total_action_required = contract_uploads + other_actions
    return {
        "contract_uploads": contract_uploads,
        "other_actions": other_actions,
        "monitoring": len(presentations) - total_action_required,
        "total_action_required": total_action_required,
    }
