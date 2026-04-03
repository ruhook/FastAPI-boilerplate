import secrets
import string
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ....core.passwords import validate_password_strength
from ....core.schemas import PersistentDeletion, TimestampSchema
from .const import ADMIN_ACCOUNT_STATUSES, DEFAULT_ADMIN_PROFILE_IMAGE_URL


def generate_temporary_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    base = "".join(secrets.choice(alphabet) for _ in range(max(length - 2, 8)))
    return f"Aa1!{base}"


class AdminUserBase(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=30, examples=["系统管理员"])]
    email: Annotated[EmailStr, Field(examples=["admin@example.com"])]
    phone: Annotated[str | None, Field(default=None, max_length=32, examples=["13800000000"])]
    note: Annotated[str | None, Field(default=None, max_length=500, examples=["负责后台权限配置"])]
    status: Annotated[str, Field(default="enabled", examples=["enabled"])]
    profile_image_url: Annotated[str, Field(default=DEFAULT_ADMIN_PROFILE_IMAGE_URL)]
    role_id: int | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ADMIN_ACCOUNT_STATUSES:
            raise ValueError("Status must be one of: enabled, disabled.")
        return value


class AdminUser(TimestampSchema, AdminUserBase, PersistentDeletion):
    username: Annotated[str, Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["admin"])]
    hashed_password: str
    is_superuser: bool = False
    last_login_at: datetime | None = None


class AdminUserReadBase(BaseModel):
    id: int
    name: str
    username: str
    email: EmailStr
    phone: str | None = None
    note: str | None = None
    status: str
    profile_image_url: str
    role_id: int | None = None
    is_superuser: bool = False
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class AdminUserDBRead(AdminUserReadBase):
    data: dict[str, Any] = Field(default_factory=dict)


class AdminUserRead(AdminUserReadBase):
    role_name: str | None = None


class AdminUserAuth(AdminUserRead):
    permissions: list[str] = Field(default_factory=list)


class AdminUserCreate(AdminUserBase):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[
        str | None, Field(default=None, min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["admin"])
    ]
    password: Annotated[str | None, Field(default=None, min_length=8, max_length=128)]

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_password_strength(value)


class AdminUserCreateInternal(BaseModel):
    name: str
    username: str
    email: EmailStr
    hashed_password: str
    phone: str | None = None
    note: str | None = None
    status: str
    profile_image_url: str
    is_superuser: bool = False
    role_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    last_login_at: datetime | None = None


class AdminUserCreateResponse(AdminUserRead):
    temporary_password: str | None = None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=2, max_length=30)]
    username: Annotated[str | None, Field(default=None, min_length=2, max_length=20, pattern=r"^[a-z0-9]+$")]
    email: Annotated[EmailStr | None, Field(default=None)]
    phone: Annotated[str | None, Field(default=None, max_length=32)]
    note: Annotated[str | None, Field(default=None, max_length=500)]
    status: Annotated[str | None, Field(default=None)]
    profile_image_url: Annotated[str | None, Field(default=None)]
    role_id: int | None = None
    password: Annotated[str | None, Field(default=None, min_length=8, max_length=128)]

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in ADMIN_ACCOUNT_STATUSES:
            raise ValueError("Status must be one of: enabled, disabled.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_password_strength(value)


class AdminUserUpdateInternal(BaseModel):
    name: str | None = None
    username: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    note: str | None = None
    status: str | None = None
    profile_image_url: str | None = None
    role_id: int | None = None
    hashed_password: str | None = None
    data: dict[str, Any] | None = None


class AdminUserDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_deleted: bool
    deleted_at: datetime


class AdminLoginRequest(BaseModel):
    username_or_email: str = Field(..., examples=["admin@example.com"])
    password: str = Field(..., examples=["ChangeMe123!"])


class AdminRefreshRequest(BaseModel):
    refresh_token: str


class AdminLogoutRequest(BaseModel):
    refresh_token: str


class AdminToken(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_token_expires_in: int
    refresh_token_expires_in: int
    user: AdminUserAuth
