from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import PersistentDeletion
from ...assets.schema import AssetRead
from .const import (
    MAIL_SIGNATURE_ADDRESS_MAX_LENGTH,
    MAIL_SIGNATURE_COMPANY_NAME_MAX_LENGTH,
    MAIL_SIGNATURE_EMAIL_MAX_LENGTH,
    MAIL_SIGNATURE_FULL_NAME_MAX_LENGTH,
    MAIL_SIGNATURE_JOB_TITLE_MAX_LENGTH,
    MAIL_SIGNATURE_LINKEDIN_LABEL_MAX_LENGTH,
    MAIL_SIGNATURE_NAME_MAX_LENGTH,
    MAIL_SIGNATURE_TEAM_MAX_LENGTH,
    MAIL_SIGNATURE_URL_MAX_LENGTH,
)


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_required(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class MailSignatureBase(BaseModel):
    name: str = Field(min_length=1, max_length=MAIL_SIGNATURE_NAME_MAX_LENGTH)
    owner: str | None = Field(default=None, max_length=MAIL_SIGNATURE_TEAM_MAX_LENGTH)
    enabled: bool = True
    full_name: str = Field(min_length=1, max_length=MAIL_SIGNATURE_FULL_NAME_MAX_LENGTH)
    job_title: str | None = Field(default=None, max_length=MAIL_SIGNATURE_JOB_TITLE_MAX_LENGTH)
    company_name: str | None = Field(default=None, max_length=MAIL_SIGNATURE_COMPANY_NAME_MAX_LENGTH)
    primary_email: str | None = Field(default=None, max_length=MAIL_SIGNATURE_EMAIL_MAX_LENGTH)
    secondary_email: str | None = Field(default=None, max_length=MAIL_SIGNATURE_EMAIL_MAX_LENGTH)
    website: str | None = Field(default=None, max_length=MAIL_SIGNATURE_URL_MAX_LENGTH)
    linkedin_label: str | None = Field(default=None, max_length=MAIL_SIGNATURE_LINKEDIN_LABEL_MAX_LENGTH)
    linkedin_url: str | None = Field(default=None, max_length=MAIL_SIGNATURE_URL_MAX_LENGTH)
    address: str | None = Field(default=None, max_length=MAIL_SIGNATURE_ADDRESS_MAX_LENGTH)
    avatar_asset_id: int | None = None
    banner_asset_id: int | None = None

    @field_validator("name", "full_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator(
        "owner",
        "job_title",
        "company_name",
        "primary_email",
        "secondary_email",
        "website",
        "linkedin_label",
        "linkedin_url",
        "address",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_optional(value)


class MailSignatureRead(MailSignatureBase):
    id: int
    html: str
    avatar_asset: AssetRead | None = None
    banner_asset: AssetRead | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailSignatureCreate(MailSignatureBase):
    model_config = ConfigDict(extra="forbid")


class MailSignatureCreateInternal(BaseModel):
    admin_user_id: int | None = None
    name: str
    owner: str | None = None
    enabled: bool = True
    full_name: str
    job_title: str | None = None
    company_name: str | None = None
    primary_email: str | None = None
    secondary_email: str | None = None
    website: str | None = None
    linkedin_label: str | None = None
    linkedin_url: str | None = None
    address: str | None = None
    avatar_asset_id: int | None = None
    banner_asset_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailSignatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAIL_SIGNATURE_NAME_MAX_LENGTH)
    owner: str | None = Field(default=None, max_length=MAIL_SIGNATURE_TEAM_MAX_LENGTH)
    enabled: bool | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=MAIL_SIGNATURE_FULL_NAME_MAX_LENGTH)
    job_title: str | None = Field(default=None, max_length=MAIL_SIGNATURE_JOB_TITLE_MAX_LENGTH)
    company_name: str | None = Field(default=None, max_length=MAIL_SIGNATURE_COMPANY_NAME_MAX_LENGTH)
    primary_email: str | None = Field(default=None, max_length=MAIL_SIGNATURE_EMAIL_MAX_LENGTH)
    secondary_email: str | None = Field(default=None, max_length=MAIL_SIGNATURE_EMAIL_MAX_LENGTH)
    website: str | None = Field(default=None, max_length=MAIL_SIGNATURE_URL_MAX_LENGTH)
    linkedin_label: str | None = Field(default=None, max_length=MAIL_SIGNATURE_LINKEDIN_LABEL_MAX_LENGTH)
    linkedin_url: str | None = Field(default=None, max_length=MAIL_SIGNATURE_URL_MAX_LENGTH)
    address: str | None = Field(default=None, max_length=MAIL_SIGNATURE_ADDRESS_MAX_LENGTH)
    avatar_asset_id: int | None = None
    banner_asset_id: int | None = None

    @field_validator("name", "full_name")
    @classmethod
    def normalize_optional_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_required(value)

    @field_validator(
        "owner",
        "job_title",
        "company_name",
        "primary_email",
        "secondary_email",
        "website",
        "linkedin_label",
        "linkedin_url",
        "address",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_optional(value)


class MailSignatureUpdateInternal(BaseModel):
    name: str | None = None
    owner: str | None = None
    enabled: bool | None = None
    full_name: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    primary_email: str | None = None
    secondary_email: str | None = None
    website: str | None = None
    linkedin_label: str | None = None
    linkedin_url: str | None = None
    address: str | None = None
    avatar_asset_id: int | None = None
    banner_asset_id: int | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class MailSignatureDelete(BaseModel):
    is_deleted: bool
    deleted_at: datetime
