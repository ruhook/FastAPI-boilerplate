from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .admin.api import router
from .admin.local_admin_bootstrap import ensure_local_admin_for_settings
from .core.config import settings
from .core.setup import create_application, lifespan_factory


@asynccontextmanager
async def admin_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    default_lifespan = lifespan_factory(settings)
    async with default_lifespan(app):
        await ensure_local_admin_for_settings(settings)
        yield


app = create_application(router=router, settings=settings, service_name="admin", lifespan=admin_lifespan)
