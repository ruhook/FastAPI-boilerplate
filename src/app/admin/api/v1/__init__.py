from fastapi import APIRouter

from ....api.v1.health import router as health_router
from .settings.assets import router as admin_assets_router
from .auth import router as admin_auth_router
from .jobs import router as admin_jobs_router
from .mail import router as admin_mail_router
from .notifications import router as admin_notifications_router
from .talents import router as admin_talents_router
from .settings import router as admin_settings_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(admin_auth_router)
router.include_router(admin_assets_router)
router.include_router(admin_jobs_router)
router.include_router(admin_talents_router)
router.include_router(admin_mail_router)
router.include_router(admin_notifications_router)
router.include_router(admin_settings_router)

__all__ = ["router"]
