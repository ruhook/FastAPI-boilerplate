from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import PersistentDeletion, TimestampSchema
from .const import (
    MailAccountProvider,
    MAIL_ACCOUNT_PROVIDER_PRESETS,
    MailAccountStatus,
)


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class MailAccountBase(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=32)
    auth_secret: str = Field(min_length=1, max_length=255)
    status: str = Field(default=MailAccountStatus.PENDING.value, min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("email", "auth_secret")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = _normalize_text(value).lower()
        if normalized not in {item.value for item in MailAccountProvider}:
            raise ValueError(f"Unsupported mail provider: {normalized}")
        return normalized

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = _normalize_text(value).lower()
        if normalized not in {item.value for item in MailAccountStatus}:
            raise ValueError(f"Unsupported mail account status: {normalized}")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class MailAccountRead(MailAccountBase):
    id: int
    smtp_username: str
    smtp_host: str
    smtp_port: int
    security_mode: str = Field(max_length=16)
    provider_label: str
    verified_at: datetime | None = None
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailAccountCreate(MailAccountBase):
    model_config = ConfigDict(extra="forbid")


class MailAccountCreateInternal(BaseModel):
    admin_user_id: int | None = None
    email: str
    provider: str
    smtp_username: str
    smtp_host: str
    smtp_port: int
    security_mode: str
    auth_secret: str
    status: str
    note: str | None = None
    verified_at: datetime | None = None
    last_tested_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=1, max_length=255)
    provider: str | None = Field(default=None, min_length=1, max_length=32)
    auth_secret: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("email", "auth_secret")
    @classmethod
    def normalize_optional_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)

    @field_validator("provider")
    @classmethod
    def validate_optional_provider(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = _normalize_text(value).lower()
        if normalized not in {item.value for item in MailAccountProvider}:
            raise ValueError(f"Unsupported mail provider: {normalized}")
        return normalized

    @field_validator("status")
    @classmethod
    def validate_optional_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = _normalize_text(value).lower()
        if normalized not in {item.value for item in MailAccountStatus}:
            raise ValueError(f"Unsupported mail account status: {normalized}")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class MailAccountUpdateInternal(BaseModel):
    email: str | None = None
    provider: str | None = None
    smtp_username: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    security_mode: str | None = None
    auth_secret: str | None = None
    status: str | None = None
    note: str | None = None
    last_tested_at: datetime | None = None
    verified_at: datetime | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class MailAccountDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_deleted: bool
    deleted_at: datetime


def resolve_mail_provider_settings(provider: str) -> tuple[str, int, str]:
    preset = MAIL_ACCOUNT_PROVIDER_PRESETS[provider]
    return str(preset["smtp_host"]), int(preset["smtp_port"]), str(preset["security_mode"])
