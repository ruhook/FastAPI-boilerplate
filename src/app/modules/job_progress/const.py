from enum import StrEnum

from ..candidate_field.const import CandidateFieldKey


class RecruitmentStage(StrEnum):
    PENDING_SCREENING = "pending_screening"
    ASSESSMENT_REVIEW = "assessment_review"
    SCREENING_PASSED = "screening_passed"
    CONTRACT_POOL = "contract_pool"
    ACTIVE = "active"
    REJECTED = "rejected"
    REPLACED = "replaced"


class RecruitmentStageView(StrEnum):
    ALL_APPLICANTS = "all_applicants"


class RecruitmentScreeningMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class JobProgressDataKey(StrEnum):
    ASSESSMENT_INVITED_AT = "assessment_invited_at"
    ASSESSMENT_INVITE_MAIL_TASK_ID = "assessment_invite_mail_task_id"
    ASSESSMENT_ATTACHMENT = "assessment_attachment"
    ASSESSMENT_ATTACHMENT_ASSET_ID = "assessment_attachment_asset_id"
    ASSESSMENT_SUBMITTED_AT = "assessment_submitted_at"
    ASSESSMENT_SUBMISSIONS = "assessment_submissions"
    ASSESSMENT_RESULT = "assessment_result"
    ASSESSMENT_REVIEW_COMMENT = "assessment_review_comment"
    ASSESSMENT_REVIEWER_ADMIN_USER_ID = "assessment_reviewer_admin_user_id"
    ASSESSMENT_REVIEWER = "assessment_reviewer"
    QA_STATUS = "qa_status"
    QA_FEEDBACK = "qa_feedback"
    ACCEPTED_RATE = "accepted_rate"
    SIGNING_STATUS = "signing_status"
    CONTRACT_NUMBER = "contract_number"
    CONTRACT_DRAFT_ATTACHMENT = "contract_draft_attachment"
    CONTRACT_DRAFT_ATTACHMENT_ASSET_ID = "contract_draft_attachment_asset_id"
    SUBMITTED_CONTRACT_ATTACHMENT = "submitted_contract_attachment"
    SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID = "submitted_contract_attachment_asset_id"
    SUBMITTED_CONTRACT_AT = "submitted_contract_at"
    CONTRACT_REVIEW = "contract_review"
    CONTRACT_RETURN_ATTACHMENT = "contract_return_attachment"
    CONTRACT_RETURN_ATTACHMENT_ASSET_ID = "contract_return_attachment_asset_id"
    ONBOARDING_STATUS = "onboarding_status"
    ONBOARDING_DATE = "onboarding_date"
    REJECTED_FROM_STAGE = "rejected_from_stage"
    REPLACEMENT_REASON = "replacement_reason"
    NOTE = "note"


JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP: dict[JobProgressDataKey, JobProgressDataKey] = {
    JobProgressDataKey.ASSESSMENT_ATTACHMENT: JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID,
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT: JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT: JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT: JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID,
}


RECRUITMENT_STAGE_CN_NAME_MAP: dict[RecruitmentStage, str] = {
    RecruitmentStage.PENDING_SCREENING: "待筛选名单",
    RecruitmentStage.ASSESSMENT_REVIEW: "测试题回收",
    RecruitmentStage.SCREENING_PASSED: "筛选通过",
    RecruitmentStage.CONTRACT_POOL: "合同库",
    RecruitmentStage.ACTIVE: "在职",
    RecruitmentStage.REJECTED: "淘汰",
    RecruitmentStage.REPLACED: "汰换",
}


def get_recruitment_stage_cn_name(stage: str) -> str:
    try:
        return RECRUITMENT_STAGE_CN_NAME_MAP[RecruitmentStage(stage)]
    except Exception:
        return stage


RECRUITMENT_STAGE_ENTRY_RULES: dict[RecruitmentStage, tuple[str, ...]] = {
    RecruitmentStage.PENDING_SCREENING: (
        "候选人初始投递后默认进入待筛选名单。",
        "如果岗位未开启自动筛选，申请会停留在待筛选名单等待人工处理。",
        "后续其他阶段的人选也可以被人工移回待筛选名单重新评估。",
    ),
    RecruitmentStage.ASSESSMENT_REVIEW: (
        "候选人初始投递只会进入待筛选名单，不会直接进入测试题回收或淘汰。",
        "岗位开启测试题环节时，候选人从 C 端上传测试题后自动进入测试题回收。",
        "测试题回收阶段支持多次上传，默认展示最新提交的测试题附件。",
    ),
    RecruitmentStage.SCREENING_PASSED: (
        "测试题回收阶段中，评审通过的人选会进入筛选通过。",
        "待筛选名单中的人选也可以由运营人工流转到筛选通过。",
    ),
    RecruitmentStage.CONTRACT_POOL: (
        "筛选通过阶段的人选开始推进签约时，进入合同库。",
    ),
    RecruitmentStage.ACTIVE: (
        "合同库阶段完成人员签约并确认入职后，进入在职。",
    ),
    RecruitmentStage.REJECTED: (
        "自动筛选未通过的人选会保留在待筛选名单，等待人工判断。",
        "测试题回收阶段执行自动化时，测试结果不通过的人选会进入淘汰。",
        "待筛选名单、测试题回收、筛选通过、合同库、在职等阶段的人选都可以被人工移入淘汰。",
    ),
    RecruitmentStage.REPLACED: (
        "仅当在职阶段的人选被标记为汰换状态时，进入汰换。",
    ),
}


RECRUITMENT_STAGE_DEFAULT_COLUMNS: dict[RecruitmentStageView | RecruitmentStage, tuple[str, ...]] = {
    RecruitmentStageView.ALL_APPLICANTS: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.NATIVE_LANGUAGES.value,
        "current_stage",
        CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value,
        CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value,
        "applied_at",
    ),
    RecruitmentStage.PENDING_SCREENING: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.WHATSAPP.value,
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.EDUCATION_STATUS.value,
        CandidateFieldKey.RESUME_ATTACHMENT.value,
        "note",
    ),
    RecruitmentStage.ASSESSMENT_REVIEW: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIVE_LANGUAGES.value,
        "assessment_attachment",
        "assessment_submitted_at",
        "assessment_result",
        "assessment_review_comment",
        "assessment_reviewer",
    ),
    RecruitmentStage.SCREENING_PASSED: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIVE_LANGUAGES.value,
        "assessment_attachment",
        "assessment_result",
        "assessment_review_comment",
        "assessment_reviewer",
        "qa_status",
        "qa_feedback",
        "accepted_rate",
        "signing_status",
        "contract_number",
        "contract_draft_attachment",
    ),
    RecruitmentStage.CONTRACT_POOL: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIVE_LANGUAGES.value,
        "accepted_rate",
        "contract_number",
        "contract_draft_attachment",
        "submitted_contract_attachment",
        "submitted_contract_at",
        "contract_review",
        "contract_return_attachment",
        "note",
    ),
    RecruitmentStage.ACTIVE: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.WHATSAPP.value,
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value,
        CandidateFieldKey.EDUCATION_STATUS.value,
        "assessment_result",
        "assessment_review_comment",
        "onboarding_status",
        "onboarding_date",
    ),
    RecruitmentStage.REJECTED: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIVE_LANGUAGES.value,
        "assessment_attachment",
        "assessment_result",
        "assessment_review_comment",
        "assessment_reviewer",
        "qa_status",
        "qa_feedback",
        "signing_status",
        "applied_at",
        "rejected_from_stage",
    ),
    RecruitmentStage.REPLACED: (
        CandidateFieldKey.FULL_NAME.value,
        CandidateFieldKey.EMAIL.value,
        CandidateFieldKey.WHATSAPP.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
        CandidateFieldKey.NATIONALITY.value,
        "onboarding_status",
        "replacement_reason",
        "applied_at",
    ),
}


RECRUITMENT_STAGE_TRANSITIONS: dict[RecruitmentStage, tuple[RecruitmentStage, ...]] = {
    RecruitmentStage.PENDING_SCREENING: (
        RecruitmentStage.SCREENING_PASSED,
        RecruitmentStage.REJECTED,
    ),
    RecruitmentStage.ASSESSMENT_REVIEW: (
        RecruitmentStage.SCREENING_PASSED,
        RecruitmentStage.PENDING_SCREENING,
        RecruitmentStage.REJECTED,
    ),
    RecruitmentStage.SCREENING_PASSED: (
        RecruitmentStage.ASSESSMENT_REVIEW,
        RecruitmentStage.REJECTED,
    ),
    RecruitmentStage.CONTRACT_POOL: (
        RecruitmentStage.ACTIVE,
        RecruitmentStage.SCREENING_PASSED,
        RecruitmentStage.REJECTED,
    ),
    RecruitmentStage.ACTIVE: (
        RecruitmentStage.REPLACED,
        RecruitmentStage.REJECTED,
    ),
    RecruitmentStage.REJECTED: (
        RecruitmentStage.PENDING_SCREENING,
    ),
    RecruitmentStage.REPLACED: (
        RecruitmentStage.PENDING_SCREENING,
    ),
}


def get_allowed_recruitment_stage_transitions(
    current_stage: str,
    *,
    assessment_enabled: bool,
) -> tuple[RecruitmentStage, ...]:
    try:
        current = RecruitmentStage(current_stage)
    except Exception:
        return ()

    transitions = list(RECRUITMENT_STAGE_TRANSITIONS.get(current, ()))
    if current == RecruitmentStage.PENDING_SCREENING:
        if assessment_enabled:
            return (
                RecruitmentStage.REJECTED,
            )
        return (
            RecruitmentStage.SCREENING_PASSED,
            RecruitmentStage.REJECTED,
        )

    if not assessment_enabled:
        transitions = [stage for stage in transitions if stage != RecruitmentStage.ASSESSMENT_REVIEW]

    return tuple(transitions)
