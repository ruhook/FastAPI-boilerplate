from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.app.admin.api import dependencies as admin_dependencies
from src.app.admin.api.v1 import auth as admin_auth
from src.app.api.dependencies import get_current_user
from src.app.core.auth_sessions import RefreshTokenReplayError
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.core.security import TokenType, create_access_token, verify_token
from src.app.modules.admin.admin_user.schema import AdminRefreshRequest
from src.app.modules.admin.admin_user.service import issue_admin_tokens
from src.app.modules.admin.role.const import ALL_ADMIN_PERMISSIONS
from src.app.modules.auth_refresh_session.model import AuthRefreshSession
from src.app.modules.user.model import User

pytestmark = pytest.mark.no_database_cleanup


class ScalarResult:
    def __init__(self, value: User | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> User | None:
        return self.value


class FakeDb:
    def __init__(self, user: User | None = None) -> None:
        self.user = user
        self.added: list[AuthRefreshSession] = []

    async def execute(self, statement: object) -> ScalarResult:
        return ScalarResult(self.user)

    def add(self, session: AuthRefreshSession) -> None:
        self.added.append(session)

    async def flush(self) -> None:
        for index, session in enumerate(self.added, start=1):
            if session.id is None:
                session.id = index


def build_user(*, token_version: int) -> User:
    user = User(
        name="Candidate",
        username="candidate",
        email="candidate@example.com",
        hashed_password="hash",
        profile_image_url="https://example.com/profile.png",
        token_version=token_version,
        data={},
    )
    user.id = 7
    return user


@pytest.mark.asyncio
async def test_web_dependency_rejects_stale_token_version() -> None:
    token = await create_access_token(
        {"sub": "7", "portal": "web", "ver": 2},
        expires_delta=timedelta(minutes=5),
    )

    with pytest.raises(UnauthorizedException, match="not authenticated"):
        await get_current_user(token, FakeDb(build_user(token_version=3)))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_web_dependency_accepts_matching_token_version() -> None:
    token = await create_access_token(
        {"sub": "7", "portal": "web", "ver": 3},
        expires_delta=timedelta(minutes=5),
    )

    current = await get_current_user(token, FakeDb(build_user(token_version=3)))  # type: ignore[arg-type]

    assert current["id"] == 7
    assert "token_version" not in current
    assert "hashed_password" not in current


@pytest.mark.asyncio
async def test_admin_dependency_rejects_stale_token_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_admin(account_id: int, db: object) -> dict[str, object]:
        return {
            "id": account_id,
            "token_version": 4,
            "status": "enabled",
            "is_superuser": True,
            "role_id": None,
        }

    monkeypatch.setattr(admin_dependencies, "_get_admin_by_id", load_admin)
    token = await create_access_token(
        {"sub": "9", "portal": "admin", "ver": 3},
        expires_delta=timedelta(minutes=5),
    )

    with pytest.raises(UnauthorizedException, match="no longer valid"):
        await admin_dependencies.get_current_admin_user(token, FakeDb())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_normal_admin_tokens_use_opaque_refresh_session() -> None:
    db = FakeDb()
    now = datetime.now(UTC)
    admin = {
        "id": 9,
        "name": "Admin",
        "username": "admin",
        "email": "admin@example.com",
        "phone": None,
        "note": None,
        "status": "enabled",
        "profile_image_url": "https://example.com/profile.png",
        "role_id": None,
        "is_superuser": True,
        "last_login_at": now,
        "created_at": now,
        "updated_at": now,
        "token_version": 5,
        "data": {},
    }

    issued = await issue_admin_tokens(
        admin,
        db,  # type: ignore[arg-type]
        ALL_ADMIN_PERMISSIONS,
        user_agent="test-browser",
    )

    assert "." not in issued.refresh_token
    assert len(db.added) == 1
    assert issued.refresh_token not in repr(db.added[0].__dict__)
    access_data = await verify_token(issued.access_token, TokenType.ACCESS)
    assert access_data is not None
    assert access_data.account_id == 9
    assert access_data.token_version == 5


@pytest.mark.asyncio
async def test_admin_refresh_replay_returns_401_response_so_revocation_can_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_local_token(token: str, expected_type: TokenType) -> None:
        return None

    async def reject_replay(*args: object, **kwargs: object) -> None:
        raise RefreshTokenReplayError("replayed")

    monkeypatch.setattr(admin_auth, "verify_token", no_local_token)
    monkeypatch.setattr(admin_auth, "rotate_refresh_session", reject_replay)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/refresh",
            "headers": [],
        }
    )

    response = await admin_auth.admin_refresh(
        request,
        AdminRefreshRequest(refresh_token="replayed-token"),
        FakeDb(),  # type: ignore[arg-type]
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
