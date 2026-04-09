from fastapi import APIRouter, Depends

from ...dependencies import require_any_admin_permission
from .....modules.candidate_field.schema import CandidateFieldCatalogItemRead
from .....modules.candidate_field.service import list_candidate_field_catalog

router = APIRouter(prefix="/candidate-fields", tags=["admin-candidate-fields"])


@router.get(
    "/catalog",
    response_model=list[CandidateFieldCatalogItemRead],
    dependencies=[Depends(require_any_admin_permission("岗位管理", "总人才库", "报名表单策略"))],
)
async def read_candidate_field_catalog() -> list[dict[str, str]]:
    return list_candidate_field_catalog()
