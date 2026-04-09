from enum import StrEnum


class OperationLogType(StrEnum):
    CANDIDATE_APPLICATION_SUBMITTED = "candidate_application_submitted"
    TALENT_PROFILE_INITIAL_AUTO_MERGE = "talent_profile_initial_auto_merge"
    TALENT_PROFILE_LATEST_APPLICATION_UPDATED = "talent_profile_latest_application_updated"
    TALENT_PROFILE_MANUAL_MERGE = "talent_profile_manual_merge"
    JOB_PROGRESS_CREATED = "job_progress_created"
    JOB_PROGRESS_STAGE_CHANGED = "job_progress_stage_changed"
    JOB_PROGRESS_ASSESSMENT_SUBMITTED = "job_progress_assessment_submitted"
    JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED = "job_progress_candidate_signed_contract_submitted"
    JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED = "job_progress_assessment_review_updated"
    JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED = "job_progress_contract_draft_uploaded"
    JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED = "job_progress_company_sealed_contract_uploaded"
