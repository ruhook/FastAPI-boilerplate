import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.auth_sessions import revoke_account_refresh_sessions
from ...core.exceptions.http_exceptions import BadRequestException, DuplicateValueException
from ...core.passwords import validate_password_strength
from ...core.security import get_password_hash
from ..referral.service import create_referral_from_code
from .crud import crud_users
from .model import User
from .schema import UserRead


async def _generate_available_username(email: str, db: AsyncSession) -> str:
    base = re.sub(r"[^a-z0-9]", "", email.split("@", 1)[0].lower())[:20] or "candidate"
    candidate = base
    suffix = 1
    while await crud_users.exists(db=db, username=candidate):
        tail = str(suffix)
        candidate = f"{base[: max(1, 20 - len(tail))]}{tail}"
        suffix += 1
    return candidate


async def register_candidate(
    *,
    name: str,
    email: str,
    password: str,
    profile_data: dict[str, Any],
    referral_code: str | None,
    db: AsyncSession,
) -> UserRead:
    if await crud_users.exists(db=db, email=email):
        raise DuplicateValueException("Email is already registered")

    savepoint = await db.begin_nested()
    created = User(
        name=name,
        username=await _generate_available_username(email, db),
        email=email,
        hashed_password=get_password_hash(password),
        profile_image_url="https://www.profileimageurl.com",
        data=profile_data,
    )
    db.add(created)
    try:
        await db.flush()
    except IntegrityError as exc:
        await savepoint.rollback()
        raise DuplicateValueException("Email or username is already registered") from exc
    else:
        await savepoint.commit()
    await create_referral_from_code(
        db=db,
        referral_code=referral_code,
        referred_user_id=int(created.id),
    )
    return UserRead.model_validate(created, from_attributes=True)


async def reset_candidate_password(*, email: str, password: str, db: AsyncSession) -> None:
    user = (
        await db.scalars(
            select(User).where(
                func.lower(User.email) == email.strip().lower(),
                User.is_deleted.is_(False),
            )
        )
    ).one_or_none()
    if user is None:
        raise BadRequestException("No candidate account was found for this email.")

    user.hashed_password = get_password_hash(validate_password_strength(password))
    user.token_version += 1
    await revoke_account_refresh_sessions(
        db,
        portal="web",
        account_id=user.id,
        reason="password_reset",
    )
    await db.flush()
