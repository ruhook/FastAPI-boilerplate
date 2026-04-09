from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...core.schemas import PersistentDeletion, TimestampSchema
from .const import JobStatus, JobWorkMode


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class JobFormStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    template_id: int


class JobAssessmentConfig(BaseModel):
    enabled: bool = True
    mail_account_id: int | None = None
    mail_template_id: int | None = None
    mail_signature_id: int | None = None
    mail_account_label: str | None = None
    mail_template_name: str | None = None
    mail_signature_name: str | None = None


class JobFormField(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    canFilter: bool
    dictionaryId: str | None = None
    options: list[str] | None = None


class JobApplicationSummary(BaseModel):
    applicants: int
    applyStarters: int
    totalViews: int
    audienceTitle: str
    audienceDescription: str


class JobAutomationRule(BaseModel):
    fieldKey: str
    fieldLabel: str
    fieldType: str
    operator: str
    value: str | int | float | list[str] | None = None
    secondValue: str | int | float | None = None


class JobAutomationRuleGroup(BaseModel):
    combinator: str = "and"
    rules: list[JobAutomationRule] = Field(default_factory=list)

    @field_validator("combinator")
    @classmethod
    def normalize_combinator(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"and", "or"}:
            raise ValueError("Unsupported automation rule combinator.")
        return normalized


class JobBase(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    company: str = Field(default="DA", min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=64)
    status: str = Field(default=JobStatus.OPEN.value, min_length=1, max_length=20)
    work_mode: str = Field(default=JobWorkMode.REMOTE.value, min_length=1, max_length=20)
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str = Field(default="Per Hour", min_length=1, max_length=20)
    description: str = Field(min_length=1)
    owner_name: str | None = Field(default=None, max_length=100)
    collaborators: list[str] = Field(default_factory=list)
    form_strategy: JobFormStrategy
    assessment_config: JobAssessmentConfig = Field(default_factory=JobAssessmentConfig)
    form_fields: list[JobFormField] = Field(default_factory=list)
    automation_rules: JobAutomationRuleGroup = Field(default_factory=JobAutomationRuleGroup)
    screening_rules: list[str] = Field(default_factory=list)
    publish_checklist: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    application_summary: JobApplicationSummary | None = None

    @field_validator("title", "company", "country", "description")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("owner_name")
    @classmethod
    def normalize_optional_owner_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip() or None

    @field_validator("collaborators", "screening_rules", "publish_checklist", "highlights")
    @classmethod
    def normalize_text_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if normalized not in {item.value for item in JobStatus}:
            raise ValueError("Unsupported job status.")
        return normalized

    @field_validator("work_mode")
    @classmethod
    def validate_work_mode(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if normalized not in {item.value for item in JobWorkMode}:
            raise ValueError("Unsupported work mode.")
        return normalized

    @field_validator("compensation_unit")
    @classmethod
    def normalize_compensation_unit(cls, value: str) -> str:
        return _normalize_text(value)

    @model_validator(mode="after")
    def validate_assessment_config(self) -> "JobBase":
        if self.assessment_config.enabled:
            if self.assessment_config.mail_account_id is None:
                raise ValueError("Assessment mail account is required when assessment is enabled.")
            if self.assessment_config.mail_template_id is None:
                raise ValueError("Assessment mail template is required when assessment is enabled.")
            if self.assessment_config.mail_signature_id is None:
                raise ValueError("Assessment mail signature is required when assessment is enabled.")
        return self


class JobRead(JobBase):
    id: int
    applicant_count: int
    owner_admin_user_id: int
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class JobListItemRead(BaseModel):
    id: int
    title: str
    company: str
    country: str
    status: str
    applicants: int
    created_at: datetime
    work_mode: str
    owner_name: str | None = None
    collaborators: list[str] = Field(default_factory=list)
    compensation: str
    assessment_enabled: bool


class JobListPage(BaseModel):
    items: list[JobListItemRead]
    total: int
    page: int
    page_size: int


class JobCreate(JobBase):
    model_config = ConfigDict(extra="forbid")


class JobCreateInternal(BaseModel):
    title: str
    company_name: str
    country: str
    status: str
    work_mode: str
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str
    description: str
    owner_admin_user_id: int
    form_template_id: int
    assessment_enabled: bool = True
    assessment_mail_account_id: int | None = None
    assessment_mail_template_id: int | None = None
    assessment_mail_signature_id: int | None = None
    applicant_count: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    company: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, min_length=1, max_length=64)
    status: str | None = Field(default=None, min_length=1, max_length=20)
    work_mode: str | None = Field(default=None, min_length=1, max_length=20)
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str | None = Field(default=None, min_length=1, max_length=20)
    description: str | None = Field(default=None, min_length=1)
    owner_name: str | None = Field(default=None, max_length=100)
    collaborators: list[str] | None = None
    form_strategy: JobFormStrategy | None = None
    assessment_config: JobAssessmentConfig | None = None
    form_fields: list[JobFormField] | None = None
    automation_rules: JobAutomationRuleGroup | None = None
    screening_rules: list[str] | None = None
    publish_checklist: list[str] | None = None
    highlights: list[str] | None = None
    application_summary: JobApplicationSummary | None = None

    @field_validator("title", "company", "country", "description")
    @classmethod
    def normalize_optional_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)

    @field_validator("owner_name")
    @classmethod
    def normalize_update_owner_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip() or None

    @field_validator("collaborators", "screening_rules", "publish_checklist", "highlights")
    @classmethod
    def normalize_optional_text_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("status")
    @classmethod
    def validate_optional_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = _normalize_text(value)
        if normalized not in {item.value for item in JobStatus}:
            raise ValueError("Unsupported job status.")
        return normalized

    @field_validator("work_mode")
    @classmethod
    def validate_optional_work_mode(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = _normalize_text(value)
        if normalized not in {item.value for item in JobWorkMode}:
            raise ValueError("Unsupported work mode.")
        return normalized


class JobUpdateInternal(BaseModel):
    title: str | None = None
    company_name: str | None = None
    country: str | None = None
    status: str | None = None
    work_mode: str | None = None
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_unit: str | None = None
    description: str | None = None
    form_template_id: int | None = None
    assessment_enabled: bool | None = None
    assessment_mail_account_id: int | None = None
    assessment_mail_template_id: int | None = None
    assessment_mail_signature_id: int | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class JobDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_deleted: bool
    deleted_at: datetime
