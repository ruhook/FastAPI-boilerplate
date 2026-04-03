from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ....core.schemas import TimestampSchema
from .const import validate_permissions


class RoleBase(BaseModel):
    name: str = Field(min_length=2, max_length=50, examples=["管理员"])
    description: str | None = Field(default=None, max_length=255, examples=["拥有后台全部权限"])
    enabled: bool = True
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def validate_role_permissions(cls, value: list[str]) -> list[str]:
        return validate_permissions(value)


class Role(TimestampSchema, RoleBase):
    pass


class RoleRead(RoleBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RoleCreate(RoleBase):
    model_config = ConfigDict(extra="forbid")


class RoleCreateInternal(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    permissions: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def validate_optional_permissions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return validate_permissions(value)


class RoleUpdateInternal(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    permissions: list[str] | None = None
    data: dict[str, Any] | None = None


class RoleDelete(BaseModel):
    pass
