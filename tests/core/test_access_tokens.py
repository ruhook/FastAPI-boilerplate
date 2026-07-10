from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.app.core.config import Settings
from src.app.core.security import (
    ALGORITHM,
    SECRET_KEY,
    TokenType,
    create_access_token,
    verify_token,
)

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio
async def test_access_token_contains_strict_account_claims() -> None:
    token = await create_access_token(
        data={"sub": "42", "portal": "web", "ver": 3},
        expires_delta=timedelta(minutes=5),
    )

    token_data = await verify_token(token, TokenType.ACCESS)

    assert token_data is not None
    assert token_data.account_id == 42
    assert token_data.portal == "web"
    assert token_data.token_version == 3
    assert token_data.token_id
    assert token_data.issued_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_claim",
    ["sub", "portal", "ver", "iat", "jti"],
)
async def test_access_token_rejects_missing_identity_claims(missing_claim: str) -> None:
    claims: dict[str, object] = {
        "sub": "42",
        "portal": "web",
        "ver": 3,
        "iat": datetime.now(UTC),
        "jti": "token-id",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "token_type": TokenType.ACCESS.value,
    }
    claims.pop(missing_claim)
    token = jwt.encode(claims, SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)

    assert await verify_token(token, TokenType.ACCESS) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sub", "version", "portal"),
    [("username", 0, "web"), ("1", -1, "web"), ("1", 0, "unknown")],
)
async def test_access_token_rejects_invalid_identity_claims(
    sub: str,
    version: int,
    portal: str,
) -> None:
    token = await create_access_token(
        data={"sub": sub, "portal": portal, "ver": version},
        expires_delta=timedelta(minutes=5),
    )

    assert await verify_token(token, TokenType.ACCESS) is None


def test_access_tokens_default_to_fifteen_minutes() -> None:
    configured = Settings(_env_file=None, ENVIRONMENT="local")

    assert configured.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert configured.ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES == 15
