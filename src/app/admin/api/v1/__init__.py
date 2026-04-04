from fastapi import APIRouter

from ....api.v1.health import router as health_router
from .auth import router as admin_auth_router
from .settings import router as admin_settings_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(admin_auth_router)
router.include_router(admin_settings_router)

__all__ = ["router"]
