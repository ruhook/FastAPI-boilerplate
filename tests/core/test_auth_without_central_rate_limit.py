from unittest.mock import AsyncMock

import pytest
from fastapi import Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from src.app.admin.api.v1 import auth as admin_auth
from src.app.api.v1 import login as web_login
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.modules.admin.admin_user.schema import AdminLoginRequest

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
