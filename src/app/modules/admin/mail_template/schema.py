import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import PersistentDeletion

TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class MailTemplateAttachmentRef(BaseModel):
    asset_id: int


class MailTemplateAttachmentRead(BaseModel):
    asset_id: int
    name: str
    mime_type: str
    preview_url: str
    download_url: str


class MailTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_id: int
    subject_template: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1)
    attachments: list[MailTemplateAttachmentRef] = Field(default_factory=list)

    @field_validator("name", "subject_template", "body_html")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_text(value)


class MailTemplateRead(MailTemplateBase):
    id: int
    attachments: list[MailTemplateAttachmentRead] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailTemplateCreate(MailTemplateBase):
    model_config = ConfigDict(extra="forbid")


class MailTemplateCreateInternal(BaseModel):
    admin_user_id: int | None = None
    category_id: int
    name: str
    subject_template: str
    body_html: str
    attachments: list[dict[str, int]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class MailTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    subject_template: str | None = Field(default=None, min_length=1, max_length=500)
    body_html: str | None = Field(default=None, min_length=1)
    attachments: list[MailTemplateAttachmentRef] | None = None

    @field_validator("name", "subject_template", "body_html")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)


class MailTemplateUpdateInternal(BaseModel):
    category_id: int | None = None
    name: str | None = None
    subject_template: str | None = None
    body_html: str | None = None
    attachments: list[dict[str, int]] | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class MailTemplateDelete(BaseModel):
    is_deleted: bool
    deleted_at: datetime


def extract_template_variables(subject_template: str, body_html: str) -> list[str]:
    variables = {match.group(1) for match in TOKEN_PATTERN.finditer(f"{subject_template}\n{body_html}")}
    return sorted(variables)
