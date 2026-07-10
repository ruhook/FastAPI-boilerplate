from datetime import UTC, datetime, timedelta

import pytest

from src.app.core.auth_sessions import (
    RefreshTokenExpiredError,
    RefreshTokenReplayError,
    create_refresh_session,
    generate_refresh_token,
    hash_refresh_token,
    rotate_refresh_session,
)
from src.app.modules.auth_refresh_session.model import AuthRefreshSession

pytestmark = pytest.mark.no_database_cleanup


class ScalarResult:
    def __init__(self, value: AuthRefreshSession | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> AuthRefreshSession | None:
        return self.value


class FakeSession:
    def __init__(self, *results: AuthRefreshSession | None) -> None:
        self.results = list(results)
        self.added: list[AuthRefreshSession] = []

    async def execute(self, statement: object) -> ScalarResult:
        value = self.results.pop(0) if self.results else None
        return ScalarResult(value)

    def add(self, session: AuthRefreshSession) -> None:
        self.added.append(session)

    async def flush(self) -> None:
        for index, session in enumerate(self.added, start=100):
            if session.id is None:
                session.id = index


def build_session(
    *,
    token: str,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    reason: str | None = None,
) -> AuthRefreshSession:
    return AuthRefreshSession(
        id=10,
        token_hash=hash_refresh_token(token),
        portal="web",
        account_id=7,
        family_id="family-id",
        parent_session_id=None,
        expires_at=expires_at,
        revoked_at=revoked_at,
        rotation_at=revoked_at if reason == "rotated" else None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        revocation_reason=reason,
        user_agent_hash=None,
        rotation_count=0,
    )


def test_refresh_tokens_are_opaque_and_only_hashes_are_stable() -> None:
    token = generate_refresh_token()

    assert len(token) >= 43
    assert "." not in token
    assert len(hash_refresh_token(token)) == 64
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert token not in hash_refresh_token(token)


@pytest.mark.asyncio
async def test_create_refresh_session_never_persists_raw_token() -> None:
    db = FakeSession()

    issued = await create_refresh_session(
        db,  # type: ignore[arg-type]
        portal="web",
        account_id=7,
        expires_delta=timedelta(days=15),
        user_agent="test-browser",
    )

    assert issued.session in db.added
    assert issued.session.token_hash == hash_refresh_token(issued.token)
    assert issued.token not in repr(issued.session.__dict__)
    assert issued.session.user_agent_hash


@pytest.mark.asyncio
async def test_rotation_revokes_parent_and_keeps_absolute_family_expiry() -> None:
    raw_token = generate_refresh_token()
    expiry = datetime.now(UTC) + timedelta(days=10)
    current = build_session(token=raw_token, expires_at=expiry)
    db = FakeSession(current)

    rotated = await rotate_refresh_session(
        db,  # type: ignore[arg-type]
        raw_token,
        portal="web",
        user_agent="new-browser",
    )

    assert current.revoked_at is not None
    assert current.rotation_at is not None
    assert current.revocation_reason == "rotated"
    assert rotated.session.parent_session_id == current.id
    assert rotated.session.family_id == current.family_id
    assert rotated.session.expires_at == expiry
    assert rotated.session.rotation_count == 1
    assert rotated.session.token_hash == hash_refresh_token(rotated.token)


@pytest.mark.asyncio
async def test_reusing_rotated_token_is_reported_as_family_replay() -> None:
    raw_token = generate_refresh_token()
    current = build_session(
        token=raw_token,
        expires_at=datetime.now(UTC) + timedelta(days=10),
        revoked_at=datetime.now(UTC),
        reason="rotated",
    )
    db = FakeSession(current)

    with pytest.raises(RefreshTokenReplayError):
        await rotate_refresh_session(db, raw_token, portal="web")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_expired_refresh_token_is_revoked_and_rejected() -> None:
    raw_token = generate_refresh_token()
    current = build_session(
        token=raw_token,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db = FakeSession(current)

    with pytest.raises(RefreshTokenExpiredError):
        await rotate_refresh_session(db, raw_token, portal="web")  # type: ignore[arg-type]

    assert current.revocation_reason == "expired"
    assert current.revoked_at is not None
