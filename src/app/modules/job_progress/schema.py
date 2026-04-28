from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from ..admin.mail_task.schema import MailRecipient
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
    qa_status: str | None = None
    qa_feedback: str | None = None


class JobProgressAssessmentReviewUpdateResponse(BaseModel):
    updated_count: int
    updated_field_keys: list[str] = Field(default_factory=list)


class JobProgressAssessmentAutomationRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)


class JobProgressAssessmentAutomationResponse(BaseModel):
    passed_count: int = 0
    rejected_count: int = 0
    untouched_count: int = 0


class JobProgressContractRecordUpdateRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)
    ensure_contract_record: bool = False
    agreement_ref_no: str | None = None
    signing_status: str | None = None
    contract_review: str | None = None
    rate: str | None = None
    end_date: date | None = None


class JobProgressContractRecordUpdateItemRead(BaseModel):
    progress_id: int
    contract_record_data: "ContractRecordDataRead | None" = None


class JobProgressContractRecordUpdateResponse(BaseModel):
    updated_count: int
    updated_field_keys: list[str] = Field(default_factory=list)
    items: list[JobProgressContractRecordUpdateItemRead] = Field(default_factory=list)


class JobProgressNotifySignContractRequest(BaseModel):
    progress_ids: list[int] = Field(min_length=1)
    account_id: int
    template_id: int | None = None
    signature_id: int | None = None
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1)
    cc_recipients: list[MailRecipient] = Field(default_factory=list)
    bcc_recipients: list[MailRecipient] = Field(default_factory=list)
    attachment_asset_ids: list[int] = Field(default_factory=list)
    render_context: dict[str, Any] = Field(default_factory=dict)


class JobProgressNotifySignContractResponse(BaseModel):
    updated_count: int
    mail_task_ids: list[int] = Field(default_factory=list)
    items: list[JobProgressContractRecordUpdateItemRead] = Field(default_factory=list)


class JobProgressContractAssetRead(BaseModel):
    asset_id: int
    name: str
    preview_url: str | None = None
    download_url: str | None = None
    mime_type: str | None = None


class ContractRecordDataRead(BaseModel):
    id: int | None = None
    user_id: int | None = None
    talent_profile_id: int | None = None
    application_id: int | None = None
    job_id: int | None = None
    job_progress_id: int | None = None
    service_customer_company_id: int | None = None
    service_customer_company_name: str | None = None
    service_customer_project_id: int | None = None
    service_customer_project_name: str | None = None
    agreement_ref_no: str | None = None
    contract_status: str | None = None
    contractor_name: str | None = None
    rate: str | None = None
    legal_entity: str | None = None
    worker_type: str | None = None
    effective_date: date | None = None
    end_date: date | None = None
    draft_contract_attachment: JobProgressContractAssetRead | None = None
    candidate_signed_contract_attachment: JobProgressContractAssetRead | None = None
    company_sealed_contract_attachment: JobProgressContractAssetRead | None = None
    contract_attachment: JobProgressContractAssetRead | None = None
    submitted_contract_at: str | None = None
    signing_status: str | None = None
    contract_review: str | None = None
    parse_status: str | None = None
    parse_error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


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
    contract_record_data: ContractRecordDataRead | None = None


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
    contract_record_data: ContractRecordDataRead | None = None


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
    contract_record_data: ContractRecordDataRead | None = None


class JobProgressCandidateSignedContractUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    candidate_signed_contract_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)
    contract_record_data: ContractRecordDataRead | None = None


class JobProgressContractDraftUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    contract_draft_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)
    contract_record_data: ContractRecordDataRead | None = None


class JobProgressCompanySealedContractUploadResponse(BaseModel):
    job_progress_id: int
    job_id: int
    application_id: int
    current_stage: str
    current_stage_cn_name: str
    company_sealed_contract_asset: AssetRead
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)
    contract_record_data: ContractRecordDataRead | None = None


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
    contract_record_data: ContractRecordDataRead | None = None


class CandidateJobApplicationListPage(BaseModel):
    items: list[CandidateJobApplicationListItemRead]
    total: int
    page: int
    page_size: int


class CandidateContractListItemRead(BaseModel):
    application_id: int
    job_progress_id: int
    job_id: int
    job_title: str
    job_company_name: str | None = None
    job_status: str
    current_stage: str
    current_stage_cn_name: str
    applied_at: datetime
    compensation_unit: str
    contract_record_data: ContractRecordDataRead


class CandidateContractListPage(BaseModel):
    items: list[CandidateContractListItemRead]
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
    contract_example_html: str = ""
    country: str
    country_label: str | None = None
    work_mode: str
    show_compensation: bool = True
    compensation_unit: str
    compensation_label: str
    assessment_enabled: bool
    application_snapshot: dict[str, Any] = Field(default_factory=dict)
    application_assets: dict[str, Any] = Field(default_factory=dict)
    process_data: dict[str, Any] = Field(default_factory=dict)
    process_assets: dict[str, Any] = Field(default_factory=dict)
    contract_record_data: ContractRecordDataRead | None = None
