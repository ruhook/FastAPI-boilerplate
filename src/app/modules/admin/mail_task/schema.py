from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .const import MailTaskStatus


def _normalize_email(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Email cannot be empty.")
    return normalized


class MailRecipient(BaseModel):
    name: str | None = None
    email: str

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class MailTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int
    template_id: int | None = None
    signature_id: int | None = None
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1)
    to_recipients: list[MailRecipient]
    cc_recipients: list[MailRecipient] = Field(default_factory=list)
    bcc_recipients: list[MailRecipient] = Field(default_factory=list)
    attachment_asset_ids: list[int] = Field(default_factory=list)
    render_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject", "body_html")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be empty.")
        return normalized


class MailTaskCreateInternal(BaseModel):
    account_id: int
    template_id: int | None = None
    signature_id: int | None = None
    subject: str
    body_html: str
    final_subject: str | None = None
    final_body_html: str | None = None
    to_recipients: list[dict[str, str | None]]
    cc_recipients: list[dict[str, str | None]] = Field(default_factory=list)
    bcc_recipients: list[dict[str, str | None]] = Field(default_factory=list)
    attachment_asset_ids: list[int] = Field(default_factory=list)
    status: str = "pending"
    error_message: str | None = None
    provider_message_id: str | None = None
    sent_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailTaskRead(BaseModel):
    id: int
    account_id: int
    account_email: str | None = None
    template_id: int | None = None
    template_name: str | None = None
    signature_id: int | None = None
    signature_name: str | None = None
    subject: str
    body_html: str
    final_subject: str | None = None
    final_body_html: str | None = None
    to_recipients: list[MailRecipient]
    cc_recipients: list[MailRecipient] = Field(default_factory=list)
    bcc_recipients: list[MailRecipient] = Field(default_factory=list)
    attachment_asset_ids: list[int] = Field(default_factory=list)
    status: str
    status_cn_name: str
    error_message: str | None = None
    provider_message_id: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailTaskUpdateInternal(BaseModel):
    status: str | None = None
    error_message: str | None = None
    final_subject: str | None = None
    final_body_html: str | None = None
    provider_message_id: str | None = None
    sent_at: datetime | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("status")
    @classmethod
    def validate_optional_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {item.value for item in MailTaskStatus}:
            raise ValueError(f"Unsupported mail task status: {normalized}")
        return normalized
