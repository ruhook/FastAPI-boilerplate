import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ..dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import BadRequestException, DuplicateValueException
from ...core.passwords import validate_password_strength
from ...core.security import get_password_hash
from ...core.utils.cache import async_get_redis
from ...modules.user.crud import crud_users
from ...modules.user.model import User
from ...modules.user.register_verification_service import (
    is_register_verification_enabled,
    send_password_reset_verification_code,
    send_register_verification_code,
    verify_password_reset_verification_code,
    verify_register_verification_code,
)
from ...modules.referral.service import create_referral_from_code
from ...modules.user.schema import UserAuth, UserCreateInternal, UserRead

router = APIRouter(prefix="/user", tags=["web-user"])


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


async def _generate_available_username(email: str, db: AsyncSession) -> str:
    base = re.sub(r"[^a-z0-9]", "", email.split("@", 1)[0].lower())[:20] or "candidate"
    candidate = base
    suffix = 1
    while await crud_users.exists(db=db, username=candidate):
        tail = str(suffix)
        candidate = f"{base[: max(1, 20 - len(tail))]}{tail}"
        suffix += 1
    return candidate


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: WebRegisterRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> dict[str, Any]:
    if is_register_verification_enabled():
        await verify_register_verification_code(
            email=str(payload.email),
            code=payload.verification_code or "",
            redis=redis,
        )

    if await crud_users.exists(db=db, email=payload.email):
        raise DuplicateValueException("Email is already registered")

    username = await _generate_available_username(payload.email, db)
    hashed_password = get_password_hash(payload.password)
    created = await crud_users.create(
        db=db,
        object=UserCreateInternal(
            name=payload.name,
            username=username,
            email=payload.email,
            hashed_password=hashed_password,
            profile_image_url="https://www.profileimageurl.com",
            data=_build_candidate_data(payload),
        ),
        schema_to_select=UserRead,
    )
    await create_referral_from_code(
        db=db,
        referral_code=payload.referral_code,
        referred_user_id=int(created["id"]),
    )
    return created


@router.post("/register/send-code", response_model=RegisterVerificationCodeResponse)
async def send_register_code(
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
    )
    return RegisterVerificationCodeResponse(
        message=(
            "Verification code sent."
            if send_result.debug_verification_code is None
            else "Local debug verification code generated."
        ),
        cooldown_seconds=send_result.cooldown_seconds,
        debug_verification_code=send_result.debug_verification_code,
    )


@router.post("/password-reset/send-code", response_model=RegisterVerificationCodeResponse)
async def send_password_reset_code(
    payload: PasswordResetCodeRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> RegisterVerificationCodeResponse:
    send_result = await send_password_reset_verification_code(
        email=str(payload.email),
        redis=redis,
        db=db,
    )
    return RegisterVerificationCodeResponse(
        message="Password reset verification code sent."
        if send_result.debug_verification_code is None
        else "Local debug password reset code generated.",
        cooldown_seconds=send_result.cooldown_seconds,
        debug_verification_code=send_result.debug_verification_code,
    )


@router.post("/password-reset/confirm")
async def confirm_password_reset(
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
    )

    result = await db.execute(
        select(User).where(
            func.lower(User.email) == normalized_email,
            User.is_deleted.is_(False),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise BadRequestException("No candidate account was found for this email.")

    user.hashed_password = get_password_hash(payload.password)
    await db.commit()
    return {"message": "Password reset successfully."}


@router.get("/me", response_model=UserAuth)
async def read_current_user(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return current_user
