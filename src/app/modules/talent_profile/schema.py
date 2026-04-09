from datetime import datetime

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
    education: str | None = None
    latest_applied_job_title: str | None = None
    resume_asset_id: int | None = None
    resume_asset_name: str | None = None
    note: str | None = None
    latest_applied_at: datetime | None = None
    created_at: datetime
    merge_strategy: str | None = None
    source_application_id: int | None = None


class TalentProfileListPage(BaseModel):
    items: list[TalentProfileListItemRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class TalentProfileMergeRequest(BaseModel):
    fields: list[str] = Field(default_factory=list)


class TalentProfileRead(TalentProfileListItemRead):
    latest_applied_job_id: int | None = None
    last_merged_at: datetime | None = None
    applications: list[CandidateApplicationSummaryRead] = Field(default_factory=list)
    pending_merge: TalentPendingMergeRead | None = None
    logs: list[OperationLogRead] = Field(default_factory=list)
