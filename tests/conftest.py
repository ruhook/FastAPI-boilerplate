from collections.abc import AsyncIterator
import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.database import local_session
from src.app.core.security import get_password_hash
from src.app.main_admin import app
from src.app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.role.model import Role
from src.app.modules.user.model import User


@pytest.fixture(scope="session")
def event_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _assert_safe_test_cleanup() -> None:
    if not settings.ALLOW_TEST_DATABASE_CLEANUP:
        raise RuntimeError(
            "Refusing to run destructive tests. Set ALLOW_TEST_DATABASE_CLEANUP=true in src/.env or the shell first."
        )

    if settings.ENVIRONMENT != EnvironmentOption.LOCAL:
        raise RuntimeError("Refusing to run destructive tests unless ENVIRONMENT=local.")

    backend = settings.DATABASE_BACKEND.lower()
    allowed_db_names = {item.strip() for item in settings.TEST_DATABASE_NAME_ALLOWLIST.split(",") if item.strip()}

    if backend == "mysql" and settings.MYSQL_SERVER not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Refusing to run destructive tests against a non-local MySQL host.")
    if backend == "mysql" and settings.MYSQL_DB not in allowed_db_names:
        raise RuntimeError("Refusing to run destructive tests against a MySQL database not in TEST_DATABASE_NAME_ALLOWLIST.")
    if backend == "postgresql" and settings.POSTGRES_SERVER not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Refusing to run destructive tests against a non-local PostgreSQL host.")
    if backend == "postgresql" and settings.POSTGRES_DB not in allowed_db_names:
        raise RuntimeError(
            "Refusing to run destructive tests against a PostgreSQL database not in TEST_DATABASE_NAME_ALLOWLIST."
        )
    if backend == "sqlite":
        sqlite_name = Path(settings.SQLITE_URI).name
        if sqlite_name not in allowed_db_names:
            raise RuntimeError("Refusing to run destructive tests against a SQLite database not in TEST_DATABASE_NAME_ALLOWLIST.")

    base_url = os.getenv("TEST_SERVER_BASE_URL")
    if base_url:
        hostname = urlparse(base_url).hostname
        if hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Refusing to send tests to a non-local TEST_SERVER_BASE_URL.")


async def _clear_tables() -> None:
    async with local_session() as session:
        await session.execute(delete(AdminUser))
        await session.execute(delete(User))
        await session.execute(delete(Role))
        await session.commit()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_database() -> AsyncIterator[None]:
    _assert_safe_test_cleanup()
    await _clear_tables()
    yield
    await _clear_tables()


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncIterator[AsyncClient]:
    base_url = os.getenv("TEST_SERVER_BASE_URL")
    if base_url:
        async with AsyncClient(base_url=base_url.rstrip("/")) as async_client:
            yield async_client
        return

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest_asyncio.fixture(loop_scope="session")
async def db_session() -> AsyncIterator[AsyncSession]:
    async with local_session() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="session")
async def superadmin_credentials(db_session: AsyncSession) -> dict[str, str | int]:
    password = "AdminPass123!"
    admin = AdminUser(
        name="Super Admin",
        username="superadmin",
        email="superadmin@example.com",
        hashed_password=get_password_hash(password),
        phone=None,
        note="bootstrap superuser",
        status="enabled",
        profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
        is_superuser=True,
        role_id=None,
        data={},
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return {
        "id": admin.id,
        "username": admin.username,
        "email": admin.email,
        "password": password,
    }


@pytest_asyncio.fixture(loop_scope="session")
async def admin_access_token(client: AsyncClient, superadmin_credentials: dict[str, str | int]) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": superadmin_credentials["username"],
            "password": superadmin_credentials["password"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest_asyncio.fixture(loop_scope="session")
async def admin_auth_headers(admin_access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_access_token}"}
