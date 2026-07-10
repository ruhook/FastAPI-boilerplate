import asyncio
import inspect
from types import SimpleNamespace

import pytest

from src.app.api.v1 import health as health_api
from src.app.core import health as health_service
from src.app.core import setup
from src.app.core.config import settings

pytestmark = pytest.mark.no_database_cleanup


def assert_ready_has_dedicated_contract() -> None:
    parameters = inspect.signature(health_api.ready).parameters
    assert "request" in parameters
    assert "redis" in parameters
    assert "db" not in parameters


@pytest.mark.asyncio
async def test_database_health_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = getattr(health_service, "_probe_database", None)
    assert probe is not None

    async def blocked_probe() -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(health_service, "_probe_database", blocked_probe)
    monkeypatch.setattr(settings, "HEALTH_CHECK_TIMEOUT_SECONDS", 0.01)
    assert await health_service.check_database_health() is False


@pytest.mark.asyncio
async def test_ready_runs_database_and_redis_checks_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    assert_ready_has_dedicated_contract()
    both_started = asyncio.Event()
    started = 0

    async def check(*args, **kwargs) -> bool:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return True

    monkeypatch.setattr(health_api, "check_database_health", check)
    monkeypatch.setattr(health_api, "check_redis_health", check)
    initialized = asyncio.Event()
    initialized.set()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(initialization_complete=initialized)))

    response = await health_api.ready(request=request, redis=SimpleNamespace())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ready_requires_initialized_app(monkeypatch: pytest.MonkeyPatch) -> None:
    assert_ready_has_dedicated_contract()

    async def healthy(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(health_api, "check_database_health", healthy)
    monkeypatch.setattr(health_api, "check_redis_health", healthy)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(initialization_complete=asyncio.Event())))

    response = await health_api.ready(request=request, redis=SimpleNamespace())
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ready_returns_503_when_dependency_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    assert_ready_has_dedicated_contract()

    async def database_unhealthy() -> bool:
        return False

    async def redis_healthy(redis) -> bool:
        return True

    monkeypatch.setattr(health_api, "check_database_health", database_unhealthy)
    monkeypatch.setattr(health_api, "check_redis_health", redis_healthy)
    initialized = asyncio.Event()
    initialized.set()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(initialization_complete=initialized)))

    response = await health_api.ready(request=request, redis=SimpleNamespace())
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_redis_pool_uses_bounded_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def capture(url: str, **kwargs: object):
        captured.update(url=url, **kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(setup.redis.ConnectionPool, "from_url", capture)
    monkeypatch.setattr(setup.redis.Redis, "from_pool", lambda pool: SimpleNamespace())
    monkeypatch.setattr(setup.cache, "pool", None)
    monkeypatch.setattr(setup.cache, "client", None)

    await setup.create_redis_cache_pool()

    assert captured["socket_connect_timeout"] == settings.REDIS_CONNECT_TIMEOUT_SECONDS
    assert captured["socket_timeout"] == settings.REDIS_SOCKET_TIMEOUT_SECONDS
