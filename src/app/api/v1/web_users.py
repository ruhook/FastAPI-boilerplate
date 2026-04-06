import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import DuplicateValueException
from ...core.security import get_password_hash
from ...modules.user.crud import crud_users
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

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty.")
        return normalized


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
) -> dict[str, Any]:
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
    return created


@router.get("/me", response_model=UserAuth)
async def read_current_user(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return current_user
