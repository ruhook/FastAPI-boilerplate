import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from src.app.admin.api.v1 import auth as admin_auth
from src.app.api.v1 import login as web_login
from src.app.core.config import settings
from src.app.core.exceptions.http_exceptions import (
    TooManyRequestsException,
    UnauthorizedException,
    UnprocessableEntityException,
)
from src.app.modules.admin.admin_user.schema import AdminLoginRequest
from src.app.modules.user import register_verification_service as verification_service

pytestmark = [pytest.mark.no_database_cleanup, pytest.mark.asyncio]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50000),
        }
    )


async def test_web_login_reaches_authentication_without_rate_limit_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticate = AsyncMock(return_value=False)
    monkeypatch.setattr(web_login, "authenticate_user", authenticate)

    for _attempt in range(6):
        with pytest.raises(UnauthorizedException):
            await web_login.login_for_access_token(
                _request(),
                Response(),
                OAuth2PasswordRequestForm(
                    username="missing@example.com",
                    password="WrongPassword123!",
                ),
                AsyncMock(),
            )

    assert authenticate.await_count == 6


async def test_admin_login_reaches_authentication_without_rate_limit_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"access_token": "token"}
    login = AsyncMock(return_value=expected)
    monkeypatch.setattr(admin_auth, "login_admin_user", login)

    for _attempt in range(6):
        result = await admin_auth.admin_login(
            _request(),
            AdminLoginRequest(
                username_or_email="missing-admin@example.com",
                password="WrongPassword123!",
            ),
            AsyncMock(),
        )

        assert result is expected

    assert login.await_count == 6


class FakeVerificationRedis:
    def __init__(self) -> None:
        self.storage: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.storage.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.storage[key] = value
        self.expirations[key] = ex

    async def delete(self, key: str) -> None:
        self.storage.pop(key, None)
        self.expirations.pop(key, None)

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -2)


async def test_verification_send_uses_redis_only_for_code_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    monkeypatch.setattr(
        verification_service.crud_users,
        "exists",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        verification_service,
        "_send_mail_sync",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        verification_service,
        "_generate_verification_code",
        lambda: "123456",
    )

    await verification_service.send_register_verification_code(
        email="candidate@example.com",
        redis=redis,  # type: ignore[arg-type]
        db=AsyncMock(),
    )

    key = verification_service._verification_cache_key("candidate@example.com")
    assert key in redis.storage


async def test_verification_check_uses_per_code_attempt_state_without_client_ip() -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    code = "123456"
    key = verification_service._verification_cache_key(email)
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": verification_service._hash_code(email, code),
            "sent_at": int(time.time()),
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = 600

    await verification_service.verify_register_verification_code(
        email=email,
        code=code,
        redis=redis,  # type: ignore[arg-type]
    )

    assert key not in redis.storage


async def test_verification_resend_cooldown_still_returns_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    key = verification_service._verification_cache_key(email)
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": "unused",
            "sent_at": int(time.time()),
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS
    monkeypatch.setattr(
        verification_service.crud_users,
        "exists",
        AsyncMock(return_value=False),
    )

    with pytest.raises(TooManyRequestsException) as caught:
        await verification_service.send_register_verification_code(
            email=email,
            redis=redis,  # type: ignore[arg-type]
            db=AsyncMock(),
        )

    assert caught.value.status_code == 429
    assert int(caught.value.headers["Retry-After"]) > 0


async def test_password_reset_resend_cooldown_returns_exact_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    fixed_now = 1_750_000_000
    cooldown = int(settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS)
    key = verification_service._verification_cache_key(
        email,
        prefix=settings.CANDIDATE_PASSWORD_RESET_VERIFICATION_REDIS_PREFIX,
    )
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": "unused",
            "sent_at": fixed_now,
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS
    existing_user_result = MagicMock()
    existing_user_result.scalar_one_or_none.return_value = 123
    db = AsyncMock()
    db.execute.return_value = existing_user_result
    monkeypatch.setattr(verification_service.time, "time", lambda: fixed_now)

    with pytest.raises(TooManyRequestsException) as caught:
        await verification_service.send_password_reset_verification_code(
            email=email,
            redis=redis,  # type: ignore[arg-type]
            db=db,
        )

    assert caught.value.status_code == 429
    assert caught.value.headers["Retry-After"] == str(cooldown)


async def test_verification_check_deletes_code_after_five_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    key = verification_service._verification_cache_key(email)
    initial_ttl = 600
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": verification_service._hash_code(email, "123456"),
            "sent_at": int(time.time()),
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = initial_ttl
    monkeypatch.setattr(settings, "CANDIDATE_REGISTER_VERIFICATION_MAX_ATTEMPTS", 5)

    for attempt_count in range(1, 5):
        with pytest.raises(UnprocessableEntityException) as caught:
            await verification_service.verify_register_verification_code(
                email=email,
                code="654321",
                redis=redis,  # type: ignore[arg-type]
            )

        assert str(caught.value.detail) == (
            f"Verification code is incorrect. {5 - attempt_count} attempt(s) remaining."
        )
        assert json.loads(redis.storage[key])["attempt_count"] == attempt_count
        assert redis.expirations[key] == initial_ttl
        assert redis.expirations[key] > 0

    with pytest.raises(UnprocessableEntityException) as caught:
        await verification_service.verify_register_verification_code(
            email=email,
            code="654321",
            redis=redis,  # type: ignore[arg-type]
        )

    assert str(caught.value.detail) == (
        "Verification code is incorrect and has expired. Please request a new one."
    )
    assert key not in redis.storage
