from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_any_admin_permission
from ....core.db.database import async_get_db
from ....modules.admin.dashboard.schema import AdminDashboardMetricsRead, DashboardRange
from ....modules.admin.dashboard.service import get_admin_dashboard_metrics

router = APIRouter(prefix="/dashboard", tags=["admin-dashboard"])


@router.get(
    "/metrics",
    response_model=AdminDashboardMetricsRead,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def read_admin_dashboard_metrics(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    range: DashboardRange = Query(default=DashboardRange.DAY),
) -> dict[str, Any]:
    return await get_admin_dashboard_metrics(
        db=db,
        current_admin=current_admin,
        period=range,
    )
