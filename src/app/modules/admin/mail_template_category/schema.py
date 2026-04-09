from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import PersistentDeletion


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class MailTemplateCategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_text(value)


class MailTemplateCategoryRead(MailTemplateCategoryBase):
    id: int
    parent_id: int | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MailTemplateCategoryCreate(MailTemplateCategoryBase):
    model_config = ConfigDict(extra="forbid")

    parent_id: int | None = None


class MailTemplateCategoryCreateInternal(BaseModel):
    admin_user_id: int | None = None
    parent_id: int | None = None
    name: str
    sort_order: int = 0
    enabled: bool = True
    data: dict[str, Any] = Field(default_factory=dict)


class MailTemplateCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)


class MailTemplateCategoryUpdateInternal(BaseModel):
    name: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class MailTemplateCategoryDelete(BaseModel):
    is_deleted: bool
    deleted_at: datetime
