from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...core.schemas import PersistentDeletion


def _normalize_required(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class AssetRead(BaseModel):
    id: int
    type: str
    module: str
    owner_type: str | None = None
    owner_id: int | None = None
    original_name: str
    mime_type: str
    file_size: int
    url: str
    preview_url: str
    download_url: str
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class AssetUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    module: str = Field(default="general", min_length=1, max_length=64)
    owner_type: str | None = Field(default=None, max_length=64)
    owner_id: int | None = None

    @field_validator("type", "module")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required(value).lower().replace(" ", "_")

    @field_validator("owner_type")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = _normalize_optional(value)
        return normalized.lower().replace(" ", "_") if normalized else None


class AssetCreateInternal(BaseModel):
    type: str
    module: str
    owner_type: str | None = None
    owner_id: int | None = None
    original_name: str = Field(max_length=255)
    storage_key: str
    mime_type: str = Field(max_length=255)
    file_size: int
    data: dict[str, Any] = Field(default_factory=dict)


class AssetUpdateInternal(BaseModel):
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class AssetDelete(PersistentDeletion):
    pass
