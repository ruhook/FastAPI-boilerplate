from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import BadRequestException
from ...core.passwords import validate_password_strength
from ...core.utils.cache import async_get_redis
from ...modules.user.auth_commands import register_candidate, reset_candidate_password
from ...modules.user.register_verification_service import (
    is_register_verification_enabled,
    send_password_reset_verification_code,
    send_register_verification_code,
    verify_password_reset_verification_code,
    verify_register_verification_code,
)
from ...modules.user.schema import UserAuth, UserRead
from ..dependencies import get_current_user

router = APIRouter(prefix="/user", tags=["web-user"])
VERIFICATION_ACCEPTED_MESSAGE = "If the address is eligible, a verification code will be sent."


class WebRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=30)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=64)
    linkedin: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=100)
    nationality: str | None = Field(default=None, max_length=100)
    native_language: str | None = Field(default=None, max_length=100)
    headline: str | None = Field(default=None, max_length=255)
    verification_code: str | None = Field(default=None, min_length=4, max_length=12)
    referral_code: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty.")
        return normalized


class RegisterVerificationCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class RegisterVerificationCodeResponse(BaseModel):
    message: str
    cooldown_seconds: int
    debug_verification_code: str | None = None


class PasswordResetCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    verification_code: str = Field(min_length=4, max_length=12)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value)


def _build_candidate_data(payload: WebRegisterRequest) -> dict[str, Any]:
    return {
        "phone": payload.phone or "",
        "linkedin": payload.linkedin or "",
        "location": payload.location or "",
        "nationality": payload.nationality or "",
        "nativeLanguage": payload.native_language or "",
        "headline": payload.headline or "",
    }


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    request: Request,
    payload: WebRegisterRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> dict[str, Any]:
    if is_register_verification_enabled():
        await verify_register_verification_code(
            email=str(payload.email),
            code=payload.verification_code or "",
            redis=redis,
            client_ip=request.client.host if request.client else "unknown",
        )

    created = await register_candidate(
        name=payload.name,
        email=str(payload.email),
        password=payload.password,
        profile_data=_build_candidate_data(payload),
        referral_code=payload.referral_code,
        db=db,
    )
    return created.model_dump()


@router.post("/register/send-code", response_model=RegisterVerificationCodeResponse)
async def send_register_code(
    request: Request,
    payload: RegisterVerificationCodeRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> RegisterVerificationCodeResponse:
    if not settings.CANDIDATE_REGISTER_VERIFICATION_ENABLED:
        raise BadRequestException("Candidate registration verification is disabled.")

    send_result = await send_register_verification_code(
        email=str(payload.email),
        redis=redis,
        db=db,
        client_ip=request.client.host if request.client else "unknown",
    )
    return RegisterVerificationCodeResponse(
        message=VERIFICATION_ACCEPTED_MESSAGE,
        cooldown_seconds=send_result.cooldown_seconds,
        debug_verification_code=send_result.debug_verification_code,
    )


@router.post("/password-reset/send-code", response_model=RegisterVerificationCodeResponse)
async def send_password_reset_code(
    request: Request,
    payload: PasswordResetCodeRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> RegisterVerificationCodeResponse:
    send_result = await send_password_reset_verification_code(
        email=str(payload.email),
        redis=redis,
        db=db,
        client_ip=request.client.host if request.client else "unknown",
    )
    return RegisterVerificationCodeResponse(
        message=VERIFICATION_ACCEPTED_MESSAGE,
        cooldown_seconds=send_result.cooldown_seconds,
        debug_verification_code=send_result.debug_verification_code,
    )


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    request: Request,
    payload: PasswordResetConfirmRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> dict[str, str]:
    if payload.password != payload.confirm_password:
        raise BadRequestException("Passwords do not match.")

    normalized_email = str(payload.email).strip().lower()
    await verify_password_reset_verification_code(
        email=normalized_email,
        code=payload.verification_code,
        redis=redis,
        client_ip=request.client.host if request.client else "unknown",
    )

    await reset_candidate_password(email=normalized_email, password=payload.password, db=db)
    return {"message": "Password reset successfully."}


@router.get("/me", response_model=UserAuth)
async def read_current_user(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return current_user
