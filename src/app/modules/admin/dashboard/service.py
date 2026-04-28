from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...candidate_application.model import CandidateApplication
from ...job_progress.const import RecruitmentStage
from ...job_progress.model import JobProgress
from ...operation_log.const import OperationLogType
from ...operation_log.model import OperationLog
from ..role.const import is_assessment_reviewer_only_permissions
from .schema import AdminDashboardMetricsRead, DashboardRange

_ADMIN_DASHBOARD_TZ = ZoneInfo("Asia/Shanghai")


def _is_assessment_reviewer_only(current_admin: dict[str, Any] | None) -> bool:
    if not current_admin:
        return False
    return is_assessment_reviewer_only_permissions(
        current_admin.get("permissions") or [],
        is_superuser=bool(current_admin.get("is_superuser")),
    )


def _get_period_start(period: DashboardRange) -> tuple[datetime, str]:
    now_local = datetime.now(_ADMIN_DASHBOARD_TZ)
    current_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == DashboardRange.DAY:
        return current_day.astimezone(UTC), "今天"
    if period == DashboardRange.WEEK:
        week_start = current_day - timedelta(days=current_day.weekday())
        return week_start.astimezone(UTC), "本周"
    month_start = current_day.replace(day=1)
    return month_start.astimezone(UTC), "本月"


async def get_admin_dashboard_metrics(
    *,
    db: AsyncSession,
    current_admin: dict[str, Any],
    period: DashboardRange,
) -> dict[str, Any]:
    period_start, period_label = _get_period_start(period)

    applications_count = int(
        (
            await db.execute(
                select(func.count(CandidateApplication.id)).where(
                    CandidateApplication.is_deleted.is_(False),
                    CandidateApplication.submitted_at >= period_start,
                )
            )
        ).scalar_one()
        or 0
    )

    assessment_filters = [
        JobProgress.is_deleted.is_(False),
        JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
        JobProgress.entered_stage_at >= period_start,
    ]
    if _is_assessment_reviewer_only(current_admin):
        assessment_filters.append(JobProgress.assessment_reviewer_admin_user_id == int(current_admin["id"]))

    assessment_count = int(
        (
            await db.execute(
                select(func.count(JobProgress.id)).where(*assessment_filters)
            )
        ).scalar_one()
        or 0
    )

    successful_signings_count = int(
        (
            await db.execute(
                select(func.count(OperationLog.id)).where(
                    OperationLog.log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
                    OperationLog.created_at >= period_start,
                )
            )
        ).scalar_one()
        or 0
    )

    active_count = int(
        (
            await db.execute(
                select(func.count(OperationLog.id)).where(
                    OperationLog.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
                    func.IFNULL(func.JSON_UNQUOTE(func.JSON_EXTRACT(OperationLog.data, "$.to_stage")), "")  # noqa: N802
                    == RecruitmentStage.ACTIVE.value,
                    OperationLog.created_at >= period_start,
                )
            )
        ).scalar_one()
        or 0
    )

    return AdminDashboardMetricsRead(
        range=period,
        items=[
            {
                "label": "测试题总量",
                "value": assessment_count,
                "note": f"{period_label}进入测试题回收且当前仍待处理的人数",
            },
            {
                "label": "申请",
                "value": applications_count,
                "note": f"{period_label}新增进入系统的岗位申请数",
            },
            {
                "label": "成功签约",
                "value": successful_signings_count,
                "note": f"{period_label}完成公司盖章回传的签约人数",
            },
            {
                "label": "在职",
                "value": active_count,
                "note": f"{period_label}进入在职阶段的人数",
            },
        ],
    ).model_dump()
