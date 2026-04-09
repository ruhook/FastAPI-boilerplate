from fastapi import APIRouter

from .assets import router as assets_router
from .health import router as health_router
from .jobs import router as jobs_router
from .login import router as login_router
from .logout import router as logout_router
from .me import router as me_router
from .web_users import router as web_users_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(assets_router)
router.include_router(login_router)
router.include_router(logout_router)
router.include_router(web_users_router)
router.include_router(jobs_router)
router.include_router(me_router)

__all__ = ["router"]
