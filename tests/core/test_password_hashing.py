from unittest.mock import AsyncMock

import pytest

from src.app.core import security

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio
async def test_argon2_hash_checks_every_byte_after_bcrypts_old_boundary() -> None:
    first = "a" * 72 + "x"
    second = "a" * 72 + "y"

    hashed = security.get_password_hash(first)

    assert hashed.startswith("$argon2id$")
    assert await security.verify_password(first, hashed) is True
    assert await security.verify_password(second, hashed) is False


@pytest.mark.asyncio
async def test_bcrypt_hash_is_rejected_without_fallback() -> None:
    legacy_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6Ttx4A0hU8JQj1U0N3Q0L7z2R3y1K"

    assert await security.verify_password("password", legacy_hash) is False


def test_hashing_rejects_password_input_over_512_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="512 UTF-8 bytes"):
        security.get_password_hash("界" * 171)


@pytest.mark.asyncio
async def test_missing_candidate_runs_dummy_password_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.crud_users, "get", AsyncMock(return_value=None))
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(security, "verify_password", verify)

    authenticated = await security.authenticate_user("missing@example.com", "password", object())

    assert authenticated is False
    verify.assert_awaited_once_with("password", security.DUMMY_PASSWORD_HASH)


@pytest.mark.asyncio
async def test_disabled_admin_runs_dummy_password_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security.crud_admin_users,
        "get",
        AsyncMock(
            return_value={
                "id": 7,
                "status": "disabled",
                "hashed_password": "must-not-be-verified",
            }
        ),
    )
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(security, "verify_password", verify)

    authenticated = await security.authenticate_admin_user("disabled@example.com", "password", object())

    assert authenticated is False
    verify.assert_awaited_once_with("password", security.DUMMY_PASSWORD_HASH)
