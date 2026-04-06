from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ....core.schemas import PersistentDeletion, TimestampSchema
from .const import (
    DICTIONARY_KEY_MAX_LENGTH,
    DICTIONARY_LABEL_MAX_LENGTH,
    DICTIONARY_OPTION_LABEL_MAX_LENGTH,
    DICTIONARY_OPTION_VALUE_MAX_LENGTH,
)


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class DictionaryOption(BaseModel):
    label: str = Field(min_length=1, max_length=DICTIONARY_OPTION_LABEL_MAX_LENGTH)
    value: str = Field(min_length=1, max_length=DICTIONARY_OPTION_VALUE_MAX_LENGTH)

    @field_validator("label", "value")
    @classmethod
    def normalize_fields(cls, value: str) -> str:
        return _normalize_text(value)


def normalize_dictionary_options(options: list[DictionaryOption]) -> list[DictionaryOption]:
    seen_values: set[str] = set()
    normalized: list[DictionaryOption] = []
    for option in options:
        if option.value in seen_values:
            raise ValueError(f"Duplicate dictionary option value: {option.value}")
        seen_values.add(option.value)
        normalized.append(option)
    return normalized


class DictionaryBase(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=DICTIONARY_KEY_MAX_LENGTH)
    label: str = Field(min_length=1, max_length=DICTIONARY_LABEL_MAX_LENGTH)
    options: list[DictionaryOption] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _normalize_text(value)

    @model_validator(mode="after")
    def validate_options(self) -> "DictionaryBase":
        self.options = normalize_dictionary_options(self.options)
        return self


class Dictionary(TimestampSchema, DictionaryBase, PersistentDeletion):
    pass


class DictionaryRead(DictionaryBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class DictionaryCreate(DictionaryBase):
    model_config = ConfigDict(extra="forbid")


class DictionaryCreateInternal(BaseModel):
    key: str | None = None
    label: str
    options: list[dict[str, str]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class DictionaryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, max_length=DICTIONARY_KEY_MAX_LENGTH)
    label: str | None = Field(default=None, min_length=1, max_length=DICTIONARY_LABEL_MAX_LENGTH)
    options: list[DictionaryOption] | None = None

    @field_validator("key")
    @classmethod
    def normalize_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("label")
    @classmethod
    def normalize_optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)

    @field_validator("options")
    @classmethod
    def validate_optional_options(cls, value: list[DictionaryOption] | None) -> list[DictionaryOption] | None:
        if value is None:
            return value
        return normalize_dictionary_options(value)


class DictionaryUpdateInternal(BaseModel):
    key: str | None = None
    label: str | None = None
    options: list[dict[str, str]] | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class DictionaryDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_deleted: bool
    deleted_at: datetime
