from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


def _validate_integer_decimal(value: Decimal) -> Decimal:
    if value != value.to_integral_value():
        raise ValueError("Output quantity must be an integer.")
    return value


class ProjectTimesheetNoteAssetRead(BaseModel):
    asset_id: int
    name: str
    preview_url: str | None = None
    download_url: str | None = None
    mime_type: str | None = None


class ProjectTimesheetWorkerOptionRead(BaseModel):
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    contract_type: str | None = None
    name: str
    email: str | None = None
    agreement_ref_no: str | None = None


class ProjectTimesheetDashboardItemRead(BaseModel):
    language: str
    customer_duration_hours: Decimal
    candidate_duration_hours: Decimal
    total_duration_hours: Decimal


class ProjectTimesheetRecordRead(BaseModel):
    id: int
    company_id: int
    project_id: int
    sub_project_name: str
    work_date: date
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    user_name: str
    user_email: str | None = None
    team_leader_user_id: int | None = None
    team_leader_name: str | None = None
    project_manager_admin_user_id: int | None = None
    project_manager_name: str | None = None
    registrar_admin_user_id: int | None = None
    registrar_name: str | None = None
    language: str
    work_type: str
    output_quantity: Decimal | None = None
    customer_human_efficiency_minutes: Decimal | None = None
    candidate_human_efficiency_minutes: Decimal | None = None
    customer_duration_hours: Decimal | None = None
    candidate_duration_hours: Decimal | None = None
    role_name: str | None = None
    non_operational_duration_hours: Decimal | None = None
    project_link: str | None = None
    poc_evaluation: str | None = None
    extra_notes: str | None = None
    note_images: list[ProjectTimesheetNoteAssetRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


class ProjectTimesheetWorkspaceRead(BaseModel):
    company_id: int
    company_name: str
    project_id: int
    project_name: str
    timesheet_languages: list[str] = Field(default_factory=list)
    timesheet_work_types: list[str] = Field(default_factory=list)
    timesheet_roles: list[str] = Field(default_factory=list)
    available_team_leaders: list[str] = Field(default_factory=list)
    available_team_leader_workers: list[ProjectTimesheetWorkerOptionRead] = Field(default_factory=list)
    available_workers: list[ProjectTimesheetWorkerOptionRead] = Field(default_factory=list)
    latest_created_at: datetime | None = None
    dashboard_items: list[ProjectTimesheetDashboardItemRead] = Field(default_factory=list)
    records: list[ProjectTimesheetRecordRead] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None


class ProjectTimesheetOverviewItemRead(BaseModel):
    company_id: int
    company_name: str
    project_id: int
    project_name: str
    record_count: int
    customer_duration_hours: Decimal
    candidate_duration_hours: Decimal
    latest_created_at: datetime | None = None


class ProjectTimesheetOverviewRead(BaseModel):
    items: list[ProjectTimesheetOverviewItemRead] = Field(default_factory=list)
    company_id: int | None = None


class ProjectTimesheetAnalyticsSummaryRead(BaseModel):
    company_count: int = 0
    project_count: int = 0
    person_count: int = 0
    sub_project_count: int = 0
    record_count: int = 0
    output_quantity: Decimal = Decimal("0.00")
    customer_duration_hours: Decimal = Decimal("0.00")
    candidate_duration_hours: Decimal = Decimal("0.00")
    non_operational_duration_hours: Decimal = Decimal("0.00")
    latest_created_at: datetime | None = None


class ProjectTimesheetAnalyticsMetricItemRead(BaseModel):
    key: str
    label: str
    company_id: int | None = None
    company_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    user_id: int | None = None
    user_email: str | None = None
    record_count: int = 0
    output_quantity: Decimal = Decimal("0.00")
    customer_duration_hours: Decimal = Decimal("0.00")
    candidate_duration_hours: Decimal = Decimal("0.00")
    non_operational_duration_hours: Decimal = Decimal("0.00")


class ProjectTimesheetAnalyticsTrendItemRead(BaseModel):
    date: date
    record_count: int = 0
    output_quantity: Decimal = Decimal("0.00")
    customer_duration_hours: Decimal = Decimal("0.00")
    candidate_duration_hours: Decimal = Decimal("0.00")
    non_operational_duration_hours: Decimal = Decimal("0.00")


class ProjectTimesheetAnalyticsFilterOptionRead(BaseModel):
    languages: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class ProjectTimesheetAnalyticsRead(BaseModel):
    company_id: int | None = None
    company_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    summary: ProjectTimesheetAnalyticsSummaryRead
    trend: list[ProjectTimesheetAnalyticsTrendItemRead] = Field(default_factory=list)
    company_breakdown: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    project_breakdown: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    language_breakdown: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    work_type_breakdown: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    role_breakdown: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    person_ranking: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    sub_project_ranking: list[ProjectTimesheetAnalyticsMetricItemRead] = Field(default_factory=list)
    filter_options: ProjectTimesheetAnalyticsFilterOptionRead = Field(
        default_factory=ProjectTimesheetAnalyticsFilterOptionRead
    )


class ProjectTimesheetBatchCreateEntry(BaseModel):
    work_date: date
    contract_record_id: int = Field(..., ge=1)
    user_id: int | None = Field(default=None, ge=1)
    work_type: str = Field(..., min_length=1, max_length=64)
    output_quantity: Decimal = Field(..., ge=0)
    customer_duration_hours: Decimal = Field(..., ge=0)
    candidate_duration_hours: Decimal = Field(..., ge=0)
    role_name: str = Field(..., min_length=1, max_length=120)
    non_operational_duration_hours: Decimal = Field(..., ge=0)
    note_asset_ids: list[int] = Field(default_factory=list)
    extra_notes: str | None = None
    poc_evaluation: str | None = None

    @field_validator("work_type", "role_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("This field is required.")
        return text

    @field_validator("output_quantity")
    @classmethod
    def validate_output_quantity_integer(cls, value: Decimal) -> Decimal:
        return _validate_integer_decimal(value)

    @field_validator("note_asset_ids")
    @classmethod
    def normalize_note_asset_ids(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            asset_id = int(item)
            if asset_id <= 0 or asset_id in seen:
                continue
            seen.add(asset_id)
            normalized.append(asset_id)
        return normalized


class ProjectTimesheetBatchCreateRequest(BaseModel):
    sub_project_name: str = Field(..., min_length=1, max_length=160)
    language: str = Field(..., min_length=1, max_length=64)
    project_link: str | None = Field(default=None, max_length=2048)
    customer_human_efficiency_minutes: Decimal = Field(..., gt=0)
    candidate_human_efficiency_minutes: Decimal = Field(..., gt=0)
    team_leader_user_id: int = Field(..., ge=0)
    project_manager_admin_user_id: int = Field(..., ge=1)
    entries: list[ProjectTimesheetBatchCreateEntry] = Field(..., min_length=1)

    @field_validator("sub_project_name", "language")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("This field is required.")
        return text

    @field_validator("project_link")
    @classmethod
    def validate_project_link(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("Project link must start with http:// or https://")
        return text


class ProjectTimesheetUpdateRequest(BaseModel):
    sub_project_name: str = Field(..., min_length=1, max_length=160)
    work_date: date
    language: str = Field(..., min_length=1, max_length=64)
    project_link: str | None = Field(default=None, max_length=2048)
    customer_human_efficiency_minutes: Decimal = Field(..., gt=0)
    candidate_human_efficiency_minutes: Decimal = Field(..., gt=0)
    team_leader_user_id: int = Field(..., ge=0)
    contract_record_id: int = Field(..., ge=1)
    user_id: int | None = Field(default=None, ge=1)
    work_type: str = Field(..., min_length=1, max_length=64)
    output_quantity: Decimal = Field(..., ge=0)
    customer_duration_hours: Decimal = Field(..., ge=0)
    candidate_duration_hours: Decimal = Field(..., ge=0)
    role_name: str = Field(..., min_length=1, max_length=120)
    non_operational_duration_hours: Decimal = Field(..., ge=0)
    note_asset_ids: list[int] = Field(default_factory=list)
    extra_notes: str | None = None
    poc_evaluation: str | None = None

    @field_validator("sub_project_name", "language", "work_type", "role_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("This field is required.")
        return text

    @field_validator("output_quantity")
    @classmethod
    def validate_output_quantity_integer(cls, value: Decimal) -> Decimal:
        return _validate_integer_decimal(value)

    @field_validator("project_link")
    @classmethod
    def validate_project_link(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("Project link must start with http:// or https://")
        return text

    @field_validator("note_asset_ids")
    @classmethod
    def normalize_note_asset_ids(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            asset_id = int(item)
            if asset_id <= 0 or asset_id in seen:
                continue
            seen.add(asset_id)
            normalized.append(asset_id)
        return normalized


class ProjectTimesheetBatchCreateResponse(BaseModel):
    created_count: int


class ProjectTimesheetBatchDeleteRequest(BaseModel):
    record_ids: list[int] = Field(..., min_length=1)

    @field_validator("record_ids")
    @classmethod
    def normalize_record_ids(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            record_id = int(item)
            if record_id <= 0 or record_id in seen:
                continue
            seen.add(record_id)
            normalized.append(record_id)
        if not normalized:
            raise ValueError("Record ids are required.")
        return normalized


class ProjectTimesheetBatchDeleteResponse(BaseModel):
    deleted_count: int


class CandidateTimesheetEntryRead(BaseModel):
    id: int
    contract_record_id: int
    project_id: int
    project_name: str | None = None
    project_code: str
    work_date: date
    hours: Decimal


class CandidateTimesheetReferralRewardRead(BaseModel):
    referred_candidate: str
    onboarding_date: date | None = None
    status: str | None = None
    work_hours: Decimal = Decimal("0.00")
    referral_earnings: Decimal = Decimal("0.00")


class CandidateTimesheetDashboardRead(BaseModel):
    latest_updated_at: datetime | None = None
    total_work_hours: Decimal = Decimal("0.00")
    referral_earnings: Decimal = Decimal("0.00")
    team_leader_bonus: Decimal = Decimal("0.00")
    estimated_income: Decimal = Decimal("0.00")


class CandidateTimesheetTeamLeaderBonusRead(BaseModel):
    month: str
    monthly_team_hours: Decimal = Decimal("0.00")
    bonus_multiplier: Decimal = Decimal("0.30")
    team_performance_bonus: Decimal = Decimal("0.00")


class CandidateTimesheetContractRead(BaseModel):
    contract_record_id: int
    previous_contract_record_id: int | None = None
    is_current: bool = True
    contract_type: str = "normal"
    agreement_ref_no: str | None = None
    contract_status: str
    job_id: int
    job_title: str | None = None
    job_country: str | None = None
    job_languages: list[str] = Field(default_factory=list)
    service_customer_company_id: int | None = None
    service_customer_company_name: str | None = None
    service_customer_project_id: int | None = None
    service_customer_project_name: str | None = None
    rate: Decimal | None = None
    rate_unit: str | None = None
    effective_date: date | None = None
    end_date: date | None = None
    work_hours: list[CandidateTimesheetEntryRead] = Field(default_factory=list)
    local_team_leader_hours: list[CandidateTimesheetEntryRead] = Field(default_factory=list)
    team_leader_bonus: CandidateTimesheetTeamLeaderBonusRead | None = None
    referral_rewards: list[CandidateTimesheetReferralRewardRead] = Field(default_factory=list)
    dashboard: CandidateTimesheetDashboardRead


class CandidateTimesheetWorkspaceRead(BaseModel):
    contracts: list[CandidateTimesheetContractRead] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    bonus_month: str | None = None
    team_leader_bonus: CandidateTimesheetTeamLeaderBonusRead | None = None
