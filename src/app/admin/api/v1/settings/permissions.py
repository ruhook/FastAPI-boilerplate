from fastapi import APIRouter, Depends

from ...dependencies import require_admin_permission
from .....modules.admin.role.const import PERMISSION_CATALOG

router = APIRouter(prefix="/permissions", tags=["admin-permissions"])


@router.get("/catalog", dependencies=[Depends(require_admin_permission("权限与角色"))])
async def read_permission_catalog() -> list[dict[str, list[str] | str]]:
    return PERMISSION_CATALOG
