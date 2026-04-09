from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..assets.schema import AssetRead


class JobProgressStageMoveRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)
    target_stage: str = Field(min_length=1)
    reason: str | None = None


class JobProgressStageMoveResponse(BaseModel):
    updated_count: int
    target_stage: str
    target_stage_cn_name: str


class JobProgressAssessmentReviewUpdateRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)
    assessment_result: str | None = None
    assessment_review_comment: str | None = None
    assessment_reviewer: str | None = None
    assessment_reviewer_admin_user_id: int | None = None


class JobProgressAssessmentReviewUpdateResponse(BaseModel):
    updated_count: int
    updated_field_keys: list[str] = Field(default_factory=list)


class JobProgressAssessmentAutomationRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)


class JobProgressAssessmentAutomationResponse(BaseModel):
    passed_count: int = 0
    rejected_count: int = 0
    untouched_count: int = 0


class JobProgressRead(BaseModel):
    id: int
    job_id: int
    user_id: int
    application_id: int
    talent_profile_id: int | None = None
    current_stage: str
    current_stage_cn_name: str
    screening_mode: str
    entered_stage_at: datetime
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class JobProgressListItemRead(BaseModel):
    id: int
    job_id: int
    user_id: int
    application_id: int
    talent_profile_id: int | None = None
    current_stage: str
    current_stage_cn_name: str
    screening_mode: str
    applied_at: datetime
    job_title: str
    job_company_name: str | None = None
    application_snapshot: dict[str, Any] = Field(default_factory=dict)
    application_assets: dict[str, Any] = Field(default_factory=dict)
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class JobProgressListPage(BaseModel):
    items: list[JobProgressListItemRead]
    total: int


class JobProgressAssessmentUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    assessment_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class JobProgressCandidateSignedContractUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    candidate_signed_contract_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class JobProgressContractDraftUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    contract_draft_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class JobProgressCompanySealedContractUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    company_sealed_contract_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class CandidateJobApplicationListItemRead(BaseModel):
    application_id: int
    job_progress_id: int
    job_id: int
    job_title: str
    job_company_name: str | None = None
    job_status: str
    current_stage: str
    current_stage_cn_name: str
    screening_mode: str
    applied_at: datetime
    application_snapshot: dict[str, Any] = Field(default_factory=dict)
    application_assets: dict[str, Any] = Field(default_factory=dict)
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)


class CandidateJobApplicationListPage(BaseModel):
    items: list[CandidateJobApplicationListItemRead]
    total: int
    page: int
    page_size: int


class CandidateJobApplicationDetailRead(BaseModel):
    application_id: int
    job_progress_id: int
    job_id: int
    job_title: str
    job_company_name: str | None = None
    job_status: str
    current_stage: str
    current_stage_cn_name: str
    screening_mode: str
    applied_at: datetime
    description_html: str
    country: str
    work_mode: str
    compensation_label: str
    assessment_enabled: bool
    application_snapshot: dict[str, Any] = Field(default_factory=dict)
    application_assets: dict[str, Any] = Field(default_factory=dict)
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)
