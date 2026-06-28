from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from ..candidate_application.schema import CandidateApplicationSummaryRead
from ..operation_log.schema import OperationLogRead


class TalentPendingMergeFieldRead(BaseModel):
    key: str
    label: str
    current_value: str | None = None
    incoming_value: str | None = None


class TalentPendingMergeRead(BaseModel):
    application_id: int
    submitted_at: datetime
    fields: list[TalentPendingMergeFieldRead] = Field(default_factory=list)


class TalentProfileListItemRead(BaseModel):
    id: int
    user_id: int
    full_name: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    nationality: str | None = None
    location: str | None = None
    native_languages: str | None = None
    additional_languages: str | None = None
    education: str | None = None
    latest_applied_job_id: int | None = None
    latest_applied_job_title: str | None = None
    resume_asset_id: int | None = None
    resume_asset_name: str | None = None
    resume_attachment_asset: "TalentAttachmentRead | None" = None
    note: str | None = None
    latest_applied_at: datetime | None = None
    created_at: datetime
    merge_strategy: str | None = None
    source_application_id: int | None = None
    english_proficiency: str | None = None
    age_range: str | None = None
    referrer_name: str | None = None
    progress_language: str | None = None
    talent_status: str | None = None
    talent_status_label: str | None = None
    talent_status_editable: bool = False
    contract_type: str | None = None
    accepted_hourly_rate: Decimal | None = None
    contract_number: str | None = None
    contract_effective_date: date | None = None
    contract_end_date: date | None = None
    company_sealed_contract_asset: "TalentAttachmentRead | None" = None
    id_attachment_asset: "TalentAttachmentRead | None" = None
    onboarding_status: str | None = None
    onboarding_date: date | None = None
    total_work_hours: Decimal | None = None
    recent_work_date: date | None = None


class TalentProfileListPage(BaseModel):
    items: list[TalentProfileListItemRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class TalentAttachmentRead(BaseModel):
    asset_id: int
    name: str
    preview_url: str | None = None
    download_url: str | None = None
    mime_type: str | None = None


class TalentStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=32)


class TalentNoteUpdateRequest(BaseModel):
    note: str | None = None


class TalentTimesheetRecordRead(BaseModel):
    id: int
    work_date: str
    sub_project_name: str | None = None
    language: str | None = None
    work_type: str | None = None
    candidate_duration_hours: Decimal | None = None
    output_quantity: Decimal | None = None
    role_name: str | None = None
    poc_evaluation: str | None = None
    extra_notes: str | None = None


class TalentPaymentRecordRead(BaseModel):
    id: int
    paid_at: datetime
    payment_type: str
    amount: Decimal
    currency: str
    project_name: str | None = None
    contract_ref_no: str | None = None
    external_transaction_no: str | None = None
    remark: str | None = None


class TalentProfileMergeRequest(BaseModel):
    fields: list[str] = Field(default_factory=list)


class TalentProfileRead(TalentProfileListItemRead):
    last_merged_at: datetime | None = None
    applications: list[CandidateApplicationSummaryRead] = Field(default_factory=list)
    timesheet_records: list[TalentTimesheetRecordRead] = Field(default_factory=list)
    payment_records: list[TalentPaymentRecordRead] = Field(default_factory=list)
    pending_merge: TalentPendingMergeRead | None = None
    logs: list[OperationLogRead] = Field(default_factory=list)
