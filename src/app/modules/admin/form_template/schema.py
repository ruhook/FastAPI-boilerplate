from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ....core.schemas import PersistentDeletion, TimestampSchema
from .const import (
    FORM_FIELD_GROUPS,
    FORM_FIELD_TYPES,
    FORM_TEMPLATE_DESCRIPTION_MAX_LENGTH,
    FORM_TEMPLATE_FIELD_KEY_MAX_LENGTH,
    FORM_TEMPLATE_FIELD_LABEL_MAX_LENGTH,
    FORM_TEMPLATE_FIELD_PLACEHOLDER_MAX_LENGTH,
    FORM_TEMPLATE_NAME_MAX_LENGTH,
)


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value cannot be empty.")
    return normalized


class FormTemplateField(BaseModel):
    key: str = Field(min_length=1, max_length=FORM_TEMPLATE_FIELD_KEY_MAX_LENGTH)
    label: str = Field(min_length=1, max_length=FORM_TEMPLATE_FIELD_LABEL_MAX_LENGTH)
    type: str
    required: bool = False
    group: str = "other"
    canFilter: bool = True
    dictionaryId: int | None = None
    placeholder: str | None = Field(default=None, max_length=FORM_TEMPLATE_FIELD_PLACEHOLDER_MAX_LENGTH)

    @field_validator("key", "label")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = _normalize_text(value).lower()
        if normalized not in FORM_FIELD_TYPES:
            raise ValueError(f"Unsupported form field type: {normalized}")
        return normalized

    @field_validator("group")
    @classmethod
    def validate_group(cls, value: str) -> str:
        normalized = _normalize_text(value).lower()
        if normalized not in FORM_FIELD_GROUPS:
            raise ValueError(f"Unsupported form field group: {normalized}")
        return normalized

    @field_validator("placeholder")
    @classmethod
    def normalize_placeholder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def normalize_dictionary_reference(self) -> "FormTemplateField":
        if self.type not in {"select", "multiselect"}:
            self.dictionaryId = None
            return self
        if self.dictionaryId is None:
            raise ValueError("Select or multiselect field must choose a dictionary.")
        return self


def parse_form_template_fields(
    raw_fields: list[dict[str, Any]] | list["FormTemplateField"],
    *,
    strict: bool,
) -> list["FormTemplateField"]:
    normalized: list[FormTemplateField] = []
    for raw_field in raw_fields:
        try:
            field = raw_field if isinstance(raw_field, FormTemplateField) else FormTemplateField.model_validate(raw_field)
        except Exception:
            if strict:
                raise
            continue
        normalized.append(field)
    return normalized


def normalize_form_template_fields(fields: list["FormTemplateField"]) -> list["FormTemplateField"]:
    seen_keys: set[str] = set()
    normalized: list[FormTemplateField] = []
    for field in fields:
        if field.key in seen_keys:
            raise ValueError(f"Duplicate form field key: {field.key}")
        seen_keys.add(field.key)
        normalized.append(field)
    return normalized


class FormTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=FORM_TEMPLATE_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=FORM_TEMPLATE_DESCRIPTION_MAX_LENGTH)
    fields: list[FormTemplateField] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_fields(self) -> "FormTemplateBase":
        self.fields = normalize_form_template_fields(self.fields)
        return self


class FormTemplate(TimestampSchema, FormTemplateBase, PersistentDeletion):
    pass


class FormTemplateRead(FormTemplateBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class FormTemplateCreate(FormTemplateBase):
    model_config = ConfigDict(extra="forbid")


class FormTemplateCreateInternal(BaseModel):
    name: str
    description: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class FormTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=FORM_TEMPLATE_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=FORM_TEMPLATE_DESCRIPTION_MAX_LENGTH)
    fields: list[FormTemplateField] | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalize_text(value)

    @field_validator("description")
    @classmethod
    def normalize_optional_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("fields")
    @classmethod
    def validate_optional_fields(cls, value: list[FormTemplateField] | None) -> list[FormTemplateField] | None:
        if value is None:
            return value
        return normalize_form_template_fields(value)


class FormTemplateUpdateInternal(BaseModel):
    name: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] | None = None
    data: dict[str, Any] | None = None
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class FormTemplateDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_deleted: bool
    deleted_at: datetime

