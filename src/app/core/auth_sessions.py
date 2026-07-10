import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..modules.auth_refresh_session.model import AuthRefreshSession
from .config import settings

AuthPortal = Literal["web", "admin"]


class RefreshSessionError(ValueError):
    """Base error for invalid refresh sessions."""


class InvalidRefreshTokenError(RefreshSessionError):
    """Raised when no matching refresh session exists."""


class RefreshTokenExpiredError(RefreshSessionError):
    """Raised when a refresh session has expired."""


class RefreshTokenReplayError(RefreshSessionError):
    """Raised when a rotated refresh token is reused."""


@dataclass(frozen=True)
class IssuedRefreshSession:
    token: str
    session: AuthRefreshSession


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_user_agent(user_agent: str | None) -> str | None:
    normalized = (user_agent or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def _refresh_lifetime(portal: AuthPortal) -> timedelta:
    days = settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS if portal == "admin" else settings.REFRESH_TOKEN_EXPIRE_DAYS
    return timedelta(days=days)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def create_refresh_session(
    db: AsyncSession,
    *,
    portal: AuthPortal,
    account_id: int,
    expires_delta: timedelta | None = None,
    family_id: str | None = None,
    parent_session_id: int | None = None,
    rotation_count: int = 0,
    user_agent: str | None = None,
    expires_at: datetime | None = None,
) -> IssuedRefreshSession:
    raw_token = generate_refresh_token()
    now = datetime.now(UTC)
    session = AuthRefreshSession(
        token_hash=hash_refresh_token(raw_token),
        portal=portal,
        account_id=account_id,
        family_id=family_id or str(uuid4()),
        parent_session_id=parent_session_id,
        expires_at=expires_at or now + (expires_delta or _refresh_lifetime(portal)),
        revoked_at=None,
        rotation_at=None,
        created_at=now,
        last_used_at=None,
        revocation_reason=None,
        user_agent_hash=hash_user_agent(user_agent),
        rotation_count=rotation_count,
    )
    db.add(session)
    await db.flush()
    return IssuedRefreshSession(token=raw_token, session=session)


async def _load_refresh_session_for_update(
    db: AsyncSession,
    raw_token: str,
    *,
    portal: AuthPortal,
) -> AuthRefreshSession | None:
    result = await db.execute(
        select(AuthRefreshSession)
        .where(
            AuthRefreshSession.token_hash == hash_refresh_token(raw_token),
            AuthRefreshSession.portal == portal,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def revoke_refresh_family(
    db: AsyncSession,
    *,
    family_id: str,
    reason: str = "replay_detected",
) -> None:
    now = datetime.now(UTC)
    await db.execute(
        update(AuthRefreshSession)
        .where(
            AuthRefreshSession.family_id == family_id,
            AuthRefreshSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=now,
            last_used_at=now,
            revocation_reason=reason,
        )
    )


async def rotate_refresh_session(
    db: AsyncSession,
    raw_token: str,
    *,
    portal: AuthPortal,
    user_agent: str | None = None,
) -> IssuedRefreshSession:
    current = await _load_refresh_session_for_update(db, raw_token, portal=portal)
    if current is None:
        raise InvalidRefreshTokenError("Invalid refresh token.")

    if current.revoked_at is not None:
        if current.revocation_reason == "rotated":
            await revoke_refresh_family(db, family_id=current.family_id)
            raise RefreshTokenReplayError("Refresh token reuse detected.")
        raise InvalidRefreshTokenError("Invalid refresh token.")

    now = datetime.now(UTC)
    if _as_utc(current.expires_at) <= now:
        current.revoked_at = now
        current.last_used_at = now
        current.revocation_reason = "expired"
        raise RefreshTokenExpiredError("Refresh token expired.")

    current.revoked_at = now
    current.rotation_at = now
    current.last_used_at = now
    current.revocation_reason = "rotated"

    return await create_refresh_session(
        db,
        portal=portal,
        account_id=current.account_id,
        family_id=current.family_id,
        parent_session_id=current.id,
        rotation_count=current.rotation_count + 1,
        user_agent=user_agent,
        expires_at=current.expires_at,
    )


async def revoke_refresh_token(
    db: AsyncSession,
    raw_token: str,
    *,
    portal: AuthPortal,
    reason: str = "logout",
) -> bool:
    session = await _load_refresh_session_for_update(db, raw_token, portal=portal)
    if session is None:
        return False
    if session.revoked_at is not None:
        if session.revocation_reason == "rotated":
            await revoke_refresh_family(db, family_id=session.family_id)
        return False
    now = datetime.now(UTC)
    session.revoked_at = now
    session.last_used_at = now
    session.revocation_reason = reason
    return True


async def revoke_account_refresh_sessions(
    db: AsyncSession,
    *,
    portal: AuthPortal,
    account_id: int,
    reason: str,
) -> None:
    now = datetime.now(UTC)
    await db.execute(
        update(AuthRefreshSession)
        .where(
            AuthRefreshSession.portal == portal,
            AuthRefreshSession.account_id == account_id,
            AuthRefreshSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=now,
            last_used_at=now,
            revocation_reason=reason,
        )
    )
