from fastapi import APIRouter

from ....api.v1.health import router as health_router
from .auth import router as admin_auth_router
from .permissions import router as admin_permissions_router
from .roles import router as admin_roles_router
from .users import router as admin_users_router

router = APIRouter(prefix="/v1")
router.include_router(health_router)
router.include_router(admin_auth_router)
router.include_router(admin_permissions_router)
router.include_router(admin_users_router)
router.include_router(admin_roles_router)

__all__ = ["router"]
