import asyncio
import logging
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text

from .config import settings
from .db.database import async_engine

LOGGER = logging.getLogger(__name__)


async def _probe_database() -> None:
    async with async_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _run_probe(probe: Callable[[], Awaitable[None]]) -> bool:
    try:
        async with asyncio.timeout(settings.HEALTH_CHECK_TIMEOUT_SECONDS):
            await probe()
        return True
    except Exception:
        LOGGER.exception("Dependency health check failed")
        return False


async def check_database_health() -> bool:
    return await _run_probe(_probe_database)


async def check_redis_health(redis: Redis) -> bool:
    async def probe() -> None:
        await redis.ping()

    return await _run_probe(probe)
