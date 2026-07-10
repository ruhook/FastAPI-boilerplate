import json
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import EnvironmentOption, settings
from src.app.core.utils.cache import async_get_redis
from src.app.main_admin import app as admin_app
from src.app.main_web import app as web_app
from src.app.modules.user import register_verification_service as verification_service
from tests.helpers.talent import create_candidate_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeRateLimitRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.keys: list[str] = []
        self.storage: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def eval(self, _script: str, _numkeys: int, key: str, window_seconds: int) -> list[int]:
        self.keys.append(key)
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], int(window_seconds)]

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


def _set_login_pair_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_LOGIN_IP_LIMIT", 100)
    monkeypatch.setattr(settings, "AUTH_LOGIN_IDENTIFIER_LIMIT", 100)
    monkeypatch.setattr(settings, "AUTH_LOGIN_PAIR_LIMIT", 1)
    monkeypatch.setattr(settings, "AUTH_LOGIN_WINDOW_SECONDS", 300)


def _install_fake_redis(application: Any, redis: FakeRateLimitRedis) -> None:
    async def override_redis() -> FakeRateLimitRedis:
        return redis

    application.dependency_overrides[async_get_redis] = override_redis


async def test_web_login_enforces_ip_identifier_pair_limit(
    web_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRateLimitRedis()
    _set_login_pair_limit(monkeypatch)
    _install_fake_redis(web_app, redis)
    try:
        first = await web_client.post(
            "/api/v1/login",
            data={"username": "missing@example.com", "password": "WrongPassword123!"},
        )
        second = await web_client.post(
            "/api/v1/login",
            data={"username": "missing@example.com", "password": "WrongPassword123!"},
        )
    finally:
        web_app.dependency_overrides.pop(async_get_redis, None)

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"] == "300"


async def test_admin_login_enforces_ip_identifier_pair_limit(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRateLimitRedis()
    _set_login_pair_limit(monkeypatch)
    _install_fake_redis(admin_app, redis)
    try:
        first = await client.post(
            "/api/v1/auth/login",
            json={"username_or_email": "missing-admin@example.com", "password": "WrongPassword123!"},
        )
        second = await client.post(
            "/api/v1/auth/login",
            json={"username_or_email": "missing-admin@example.com", "password": "WrongPassword123!"},
        )
    finally:
        admin_app.dependency_overrides.pop(async_get_redis, None)

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"] == "300"


async def test_local_virtual_admin_bypass_is_not_rate_limited(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRateLimitRedis()
    _set_login_pair_limit(monkeypatch)
    monkeypatch.setattr(settings, "ENABLE_LOCAL_AUTH_BYPASS", True)
    _install_fake_redis(admin_app, redis)
    try:
        first = await client.post(
            "/api/v1/auth/login",
            json={"username_or_email": "HaokangImport", "password": "anything"},
        )
        second = await client.post(
            "/api/v1/auth/login",
            json={"username_or_email": "HaokangImport", "password": "anything"},
        )
    finally:
        admin_app.dependency_overrides.pop(async_get_redis, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert redis.keys == []


async def test_registration_send_code_does_not_reveal_existing_account(
    web_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing, _ = await create_candidate_user(db_session, suffix="enumeration-register", name="Existing")
    redis = FakeRateLimitRedis()
    monkeypatch.setattr(settings, "CANDIDATE_REGISTER_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(verification_service, "_send_mail_sync", lambda *_args, **_kwargs: None)
    _install_fake_redis(web_app, redis)
    try:
        existing_response = await web_client.post(
            "/api/v1/user/register/send-code",
            json={"email": existing.email},
        )
        new_response = await web_client.post(
            "/api/v1/user/register/send-code",
            json={"email": "new-registration@example.com"},
        )
    finally:
        web_app.dependency_overrides.pop(async_get_redis, None)

    assert existing_response.status_code == 200
    assert new_response.status_code == 200
    assert existing_response.json() == new_response.json() == {
        "message": "If the address is eligible, a verification code will be sent.",
        "cooldown_seconds": settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS,
        "debug_verification_code": None,
    }


async def test_password_reset_send_code_does_not_reveal_missing_account(
    web_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing, _ = await create_candidate_user(db_session, suffix="enumeration-reset", name="Existing")
    redis = FakeRateLimitRedis()
    monkeypatch.setattr(verification_service, "_send_mail_sync", lambda *_args, **_kwargs: None)
    _install_fake_redis(web_app, redis)
    try:
        existing_response = await web_client.post(
            "/api/v1/user/password-reset/send-code",
            json={"email": existing.email},
        )
        missing_response = await web_client.post(
            "/api/v1/user/password-reset/send-code",
            json={"email": "missing-reset@example.com"},
        )
    finally:
        web_app.dependency_overrides.pop(async_get_redis, None)

    assert existing_response.status_code == 200
    assert missing_response.status_code == 200
    assert existing_response.json() == missing_response.json() == {
        "message": "If the address is eligible, a verification code will be sent.",
        "cooldown_seconds": settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS,
        "debug_verification_code": None,
    }


async def test_non_local_smtp_failure_does_not_leak_exception_text(
    web_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_mail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("smtp-secret-detail")

    redis = FakeRateLimitRedis()
    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentOption.STAGING)
    monkeypatch.setattr(settings, "CANDIDATE_REGISTER_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(verification_service, "_send_mail_sync", fail_mail)
    _install_fake_redis(web_app, redis)
    try:
        response = await web_client.post(
            "/api/v1/user/register/send-code",
            json={"email": "smtp-failure@example.com"},
        )
    finally:
        web_app.dependency_overrides.pop(async_get_redis, None)

    assert response.status_code == 200
    assert "smtp-secret-detail" not in response.text
    assert response.json()["message"] == "If the address is eligible, a verification code will be sent."


async def test_verification_code_hash_uses_domain_separated_application_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("first-application-secret"))
    monkeypatch.setattr(settings, "CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET", SecretStr("smtp-one"))
    first = verification_service._hash_code("User@Example.com", "123456")

    monkeypatch.setattr(settings, "CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET", SecretStr("smtp-two"))
    after_smtp_change = verification_service._hash_code("User@Example.com", "123456")
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("second-application-secret"))
    after_application_secret_change = verification_service._hash_code("User@Example.com", "123456")

    assert after_smtp_change == first
    assert after_application_secret_change != first


async def test_verification_check_applies_ip_and_identifier_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRateLimitRedis()
    email = "verify-limit@example.com"
    code = "123456"
    cache_key = verification_service._verification_cache_key(email)
    redis.storage[cache_key] = json.dumps(
        {
            "email": email,
            "code_hash": verification_service._hash_code(email, code),
            "sent_at": 1,
            "attempt_count": 0,
        }
    )
    redis.expirations[cache_key] = 600
    monkeypatch.setattr(settings, "AUTH_VERIFICATION_CHECK_IP_LIMIT", 100)
    monkeypatch.setattr(settings, "AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT", 100)

    await verification_service.verify_register_verification_code(
        email=email,
        code=code,
        redis=redis,  # type: ignore[arg-type]
        client_ip="203.0.113.9",
    )

    assert len(redis.keys) == 2
