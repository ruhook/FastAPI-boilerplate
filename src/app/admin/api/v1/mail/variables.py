from fastapi import APIRouter, Depends

from ...dependencies import require_admin_permission
from .....modules.admin.mail_template.const import MAIL_VARIABLE_CATALOG

router = APIRouter(prefix="/variables")


@router.get("", dependencies=[Depends(require_admin_permission("邮件与模板"))])
async def read_mail_variables() -> dict[str, list[dict[str, str]]]:
    return {"items": MAIL_VARIABLE_CATALOG}
