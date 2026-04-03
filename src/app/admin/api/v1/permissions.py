from fastapi import APIRouter, Depends

from ..dependencies import get_current_admin_user
from ....modules.admin.role.const import PERMISSION_CATALOG

router = APIRouter(prefix="/permissions", tags=["admin-permissions"])


@router.get("/catalog", dependencies=[Depends(get_current_admin_user)])
async def read_permission_catalog() -> list[dict[str, list[str] | str]]:
    return PERMISSION_CATALOG
