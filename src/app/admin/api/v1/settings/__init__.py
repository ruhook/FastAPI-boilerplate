from fastapi import APIRouter

from .accounts import router as accounts_router
from .dictionaries import router as dictionaries_router
from .form_templates import router as form_templates_router
from .permissions import router as permissions_router
from .roles import router as roles_router

router = APIRouter(prefix="/settings")
router.include_router(accounts_router)
router.include_router(roles_router)
router.include_router(permissions_router)
router.include_router(dictionaries_router)
router.include_router(form_templates_router)

__all__ = ["router"]

