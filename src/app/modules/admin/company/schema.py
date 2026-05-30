from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import PersistentDeletion
from ...assets.schema import AssetRead


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


class CompanyBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    logo_asset_id: int | None = None
    timesheet_languages: list[str] = Field(default_factory=list)
    timesheet_work_types: list[str] = Field(default_factory=list)
    timesheet_roles: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _normalize_optional(value)

    @field_validator("timesheet_languages", "timesheet_work_types", "timesheet_roles")
    @classmethod
    def normalize_string_list(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = _normalize_optional(item)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized


class CompanyRead(CompanyBase):
    id: int
    logo_asset: AssetRead | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CompanyProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_required(value)


class CompanyProjectRead(CompanyProjectBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CompanyProjectMenuProjectRead(BaseModel):
    id: int
    company_id: int
    name: str


class CompanyProjectMenuCompanyRead(BaseModel):
    id: int
    name: str
    projects: list[CompanyProjectMenuProjectRead] = Field(default_factory=list)


class CompanyProjectCreate(CompanyProjectBase):
    model_config = ConfigDict(extra="forbid")


class CompanyProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_required(value)


class CompanyCreate(CompanyBase):
    model_config = ConfigDict(extra="forbid")


class CompanyCreateInternal(BaseModel):
    name: str
    description: str | None = None
    logo_asset_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CompanyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    logo_asset_id: int | None = None
    timesheet_languages: list[str] | None = None
    timesheet_work_types: list[str] | None = None
    timesheet_roles: list[str] | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_required(value)

    @field_validator("description")
    @classmethod
    def normalize_optional_description(cls, value: str | None) -> str | None:
        return _normalize_optional(value)

    @field_validator("timesheet_languages", "timesheet_work_types", "timesheet_roles")
    @classmethod
    def normalize_optional_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = _normalize_optional(item)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized


class CompanyUpdateInternal(BaseModel):
    name: str | None = None
    description: str | None = None
    logo_asset_id: int | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class CompanyDelete(PersistentDeletion):
    model_config = ConfigDict(extra="forbid")
