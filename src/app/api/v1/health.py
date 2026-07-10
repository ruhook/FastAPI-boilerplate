import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from ...core.config import settings
from ...core.health import check_database_health, check_redis_health
from ...core.schemas import HealthCheck, ReadyCheck
from ...core.utils.cache import async_get_redis

router = APIRouter(tags=["health"])

STATUS_HEALTHY = "healthy"
STATUS_UNHEALTHY = "unhealthy"

LOGGER = logging.getLogger(__name__)


@router.get("/health", response_model=HealthCheck)
async def health():
    http_status = status.HTTP_200_OK
    response = {
        "status": STATUS_HEALTHY,
        "environment": settings.ENVIRONMENT.value,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    return JSONResponse(status_code=http_status, content=response)


@router.get("/ready", response_model=ReadyCheck)
async def ready(
    request: Request,
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> JSONResponse:
    database_status, redis_status = await asyncio.gather(
        check_database_health(),
        check_redis_health(redis),
    )
    LOGGER.debug("Database health check status: %s", database_status)
    LOGGER.debug("Redis health check status: %s", redis_status)

    initialization = getattr(request.app.state, "initialization_complete", None)
    app_status = bool(initialization is not None and initialization.is_set())
    overall_status = STATUS_HEALTHY if app_status and database_status and redis_status else STATUS_UNHEALTHY
    http_status = status.HTTP_200_OK if overall_status == STATUS_HEALTHY else status.HTTP_503_SERVICE_UNAVAILABLE

    response = {
        "status": overall_status,
        "environment": settings.ENVIRONMENT.value,
        "version": settings.APP_VERSION,
        "app": STATUS_HEALTHY if app_status else STATUS_UNHEALTHY,
        "database": STATUS_HEALTHY if database_status else STATUS_UNHEALTHY,
        "redis": STATUS_HEALTHY if redis_status else STATUS_UNHEALTHY,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    return JSONResponse(status_code=http_status, content=response)
