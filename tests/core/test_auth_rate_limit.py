from typing import Any

import pytest
from redis.exceptions import RedisError

from src.app.core.auth_rate_limit import AuthRateLimitAction, enforce_auth_rate_limit
from src.app.core.config import EnvironmentOption, settings
from src.app.core.exceptions.http_exceptions import (
    AuthRateLimitUnavailableException,
    TooManyRequestsException,
)

pytestmark = pytest.mark.no_database_cleanup


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.keys: list[str] = []

    async def eval(self, _script: str, _numkeys: int, key: str, window_seconds: int) -> list[int]:
        self.keys.append(key)
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], int(window_seconds)]


class FailingRedis:
    async def eval(self, *_args: Any) -> list[int]:
        raise RedisError("redis unavailable")


@pytest.mark.asyncio
async def test_login_pair_limit_returns_retry_after_without_storing_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(settings, "AUTH_LOGIN_IP_LIMIT", 100)
    monkeypatch.setattr(settings, "AUTH_LOGIN_IDENTIFIER_LIMIT", 100)
    monkeypatch.setattr(settings, "AUTH_LOGIN_PAIR_LIMIT", 1)
    monkeypatch.setattr(settings, "AUTH_LOGIN_WINDOW_SECONDS", 300)

    await enforce_auth_rate_limit(
        redis,  # type: ignore[arg-type]
        action=AuthRateLimitAction.LOGIN,
        client_ip="127.0.0.1",
        identifier="User@Example.com",
    )

    with pytest.raises(TooManyRequestsException) as caught:
        await enforce_auth_rate_limit(
            redis,  # type: ignore[arg-type]
            action=AuthRateLimitAction.LOGIN,
            client_ip="127.0.0.1",
            identifier="user@example.com",
        )

    assert caught.value.headers == {"Retry-After": "300"}
    assert "user@example.com" not in " ".join(redis.keys)
    assert len(redis.keys) == 6


@pytest.mark.asyncio
async def test_local_environment_fails_open_when_rate_limit_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentOption.LOCAL)

    await enforce_auth_rate_limit(
        FailingRedis(),  # type: ignore[arg-type]
        action=AuthRateLimitAction.LOGIN,
        client_ip="127.0.0.1",
        identifier="local@example.com",
    )

    assert "rate limit unavailable" in caplog.text.lower()


@pytest.mark.asyncio
async def test_non_local_environment_fails_closed_when_rate_limit_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentOption.STAGING)

    with pytest.raises(AuthRateLimitUnavailableException):
        await enforce_auth_rate_limit(
            FailingRedis(),  # type: ignore[arg-type]
            action=AuthRateLimitAction.LOGIN,
            client_ip="198.51.100.4",
            identifier="user@example.com",
        )
