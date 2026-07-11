from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from .model import ProjectTimesheetRecord
from .schema import (
    ProjectTimesheetAnalyticsFilterOptionRead,
    ProjectTimesheetAnalyticsMetricItemRead,
    ProjectTimesheetAnalyticsRead,
    ProjectTimesheetAnalyticsSummaryRead,
    ProjectTimesheetAnalyticsTrendItemRead,
)
from .serialization import (
    _quantize_candidate_duration_hours,
    _quantize_customer_duration_hours,
    _quantize_hours,
    _to_decimal,
    _zero_decimal,
)


def _build_timesheet_analytics_conditions(
    *,
    company_id: int | None = None,
    project_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    language: str | None = None,
    work_type: str | None = None,
    role_name: str | None = None,
    keyword: str | None = None,
) -> list[Any]:
    conditions: list[Any] = [
        ProjectTimesheetRecord.is_deleted.is_(False),
        AdminCompany.is_deleted.is_(False),
        AdminCompanyProject.is_deleted.is_(False),
    ]
    if company_id is not None:
        conditions.append(ProjectTimesheetRecord.company_id == company_id)
    if project_id is not None:
        conditions.append(ProjectTimesheetRecord.project_id == project_id)
    if start_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date >= start_date)
    if end_date is not None:
        conditions.append(ProjectTimesheetRecord.work_date <= end_date)
    if language:
        conditions.append(ProjectTimesheetRecord.language == language)
    if work_type:
        conditions.append(ProjectTimesheetRecord.work_type == work_type)
    if role_name:
        conditions.append(ProjectTimesheetRecord.role_name == role_name)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                AdminCompany.name.ilike(like),
                AdminCompanyProject.name.ilike(like),
                ProjectTimesheetRecord.sub_project_name.ilike(like),
                ProjectTimesheetRecord.user_name_snapshot.ilike(like),
                ProjectTimesheetRecord.user_email_snapshot.ilike(like),
                ProjectTimesheetRecord.language.ilike(like),
                ProjectTimesheetRecord.work_type.ilike(like),
                ProjectTimesheetRecord.role_name.ilike(like),
                ProjectTimesheetRecord.project_link.ilike(like),
                ProjectTimesheetRecord.extra_notes.ilike(like),
                ProjectTimesheetRecord.poc_evaluation.ilike(like),
            )
        )
    return conditions


def _timesheet_analytics_select_from(statement: Any) -> Any:
    return (
        statement.select_from(ProjectTimesheetRecord)
        .join(AdminCompany, AdminCompany.id == ProjectTimesheetRecord.company_id)
        .join(
            AdminCompanyProject,
            (AdminCompanyProject.id == ProjectTimesheetRecord.project_id)
            & (AdminCompanyProject.company_id == AdminCompany.id),
        )
    )


def _build_timesheet_metric_item(
    *,
    key: str,
    label: str | None,
    record_count: int | None,
    output_quantity: Any,
    customer_duration_hours: Any,
    candidate_duration_hours: Any,
    non_operational_duration_hours: Any,
    company_id: int | None = None,
    company_name: str | None = None,
    project_id: int | None = None,
    project_name: str | None = None,
    user_id: int | None = None,
    user_email: str | None = None,
) -> ProjectTimesheetAnalyticsMetricItemRead:
    return ProjectTimesheetAnalyticsMetricItemRead(
        key=key,
        label=label or "未填写",
        company_id=company_id,
        company_name=company_name,
        project_id=project_id,
        project_name=project_name,
        user_id=user_id,
        user_email=user_email,
        record_count=int(record_count or 0),
        output_quantity=_quantize_hours(_to_decimal(output_quantity)) or _zero_decimal(),
        customer_duration_hours=(
            _quantize_customer_duration_hours(_to_decimal(customer_duration_hours)) or _zero_decimal()
        ),
        candidate_duration_hours=(
            _quantize_candidate_duration_hours(_to_decimal(candidate_duration_hours)) or _zero_decimal()
        ),
        non_operational_duration_hours=_quantize_hours(_to_decimal(non_operational_duration_hours)) or _zero_decimal(),
    )


def _build_timesheet_analytics_summary(row: tuple[Any, ...]) -> ProjectTimesheetAnalyticsSummaryRead:
    (
        record_count,
        company_count,
        project_count,
        person_count,
        sub_project_count,
        output_quantity,
        customer_hours,
        candidate_hours,
        non_operational_hours,
        latest_created_at,
    ) = row
    return ProjectTimesheetAnalyticsSummaryRead(
        company_count=int(company_count or 0),
        project_count=int(project_count or 0),
        person_count=int(person_count or 0),
        sub_project_count=int(sub_project_count or 0),
        record_count=int(record_count or 0),
        output_quantity=_quantize_hours(_to_decimal(output_quantity)) or _zero_decimal(),
        customer_duration_hours=_quantize_customer_duration_hours(_to_decimal(customer_hours)) or _zero_decimal(),
        candidate_duration_hours=_quantize_candidate_duration_hours(_to_decimal(candidate_hours)) or _zero_decimal(),
        non_operational_duration_hours=_quantize_hours(_to_decimal(non_operational_hours)) or _zero_decimal(),
        latest_created_at=latest_created_at,
    )


async def _load_timesheet_filter_options(
    *,
    db: AsyncSession,
    conditions: list[Any],
) -> ProjectTimesheetAnalyticsFilterOptionRead:
    async def load_distinct(column: Any) -> list[str]:
        result = await db.execute(
            _timesheet_analytics_select_from(select(column))
            .where(*conditions, column.is_not(None), column != "")
            .distinct()
            .order_by(column.asc())
        )
        return [str(item).strip() for item in result.scalars().all() if str(item or "").strip()]

    return ProjectTimesheetAnalyticsFilterOptionRead(
        languages=await load_distinct(ProjectTimesheetRecord.language),
        work_types=await load_distinct(ProjectTimesheetRecord.work_type),
        roles=await load_distinct(ProjectTimesheetRecord.role_name),
    )


async def list_project_timesheet_analytics(
    *,
    db: AsyncSession,
    company_id: int | None = None,
    project_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    language: str | None = None,
    work_type: str | None = None,
    role_name: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise BadRequestException("Start date cannot be later than end date.")

    scope_company_name: str | None = None
    scope_project_name: str | None = None
    if company_id is not None:
        company = await db.get(AdminCompany, company_id)
        if company is None or company.is_deleted:
            raise NotFoundException("Company not found.")
        scope_company_name = company.name
    if project_id is not None:
        project_result = await db.execute(
            select(AdminCompanyProject).where(
                AdminCompanyProject.id == project_id,
                AdminCompanyProject.is_deleted.is_(False),
                *([AdminCompanyProject.company_id == company_id] if company_id is not None else []),
            )
        )
        project = project_result.scalar_one_or_none()
        if project is None:
            raise NotFoundException("Project not found.")
        scope_project_name = project.name
        if company_id is None:
            company_id = int(project.company_id)
            company = await db.get(AdminCompany, company_id)
            scope_company_name = company.name if company is not None and not company.is_deleted else None

    conditions = _build_timesheet_analytics_conditions(
        company_id=company_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        language=(language or "").strip() or None,
        work_type=(work_type or "").strip() or None,
        role_name=(role_name or "").strip() or None,
        keyword=(keyword or "").strip() or None,
    )

    summary_result = await db.execute(
        _timesheet_analytics_select_from(
            select(
                func.count(ProjectTimesheetRecord.id),
                func.count(func.distinct(ProjectTimesheetRecord.company_id)),
                func.count(func.distinct(ProjectTimesheetRecord.project_id)),
                func.count(func.distinct(ProjectTimesheetRecord.user_id)),
                func.count(func.distinct(ProjectTimesheetRecord.sub_project_name)),
                func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
                func.max(func.coalesce(ProjectTimesheetRecord.updated_at, ProjectTimesheetRecord.created_at)),
            )
        ).where(*conditions)
    )
    summary = _build_timesheet_analytics_summary(summary_result.one())

    trend_result = await db.execute(
        _timesheet_analytics_select_from(
            select(
                ProjectTimesheetRecord.work_date,
                func.count(ProjectTimesheetRecord.id),
                func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0),
                func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
            )
        )
        .where(*conditions)
        .group_by(ProjectTimesheetRecord.work_date)
        .order_by(ProjectTimesheetRecord.work_date.asc())
    )
    trend = [
        ProjectTimesheetAnalyticsTrendItemRead(
            date=raw_date,
            record_count=int(raw_count or 0),
            output_quantity=_quantize_hours(_to_decimal(raw_output)) or _zero_decimal(),
            customer_duration_hours=_quantize_customer_duration_hours(_to_decimal(raw_customer)) or _zero_decimal(),
            candidate_duration_hours=_quantize_candidate_duration_hours(_to_decimal(raw_candidate)) or _zero_decimal(),
            non_operational_duration_hours=_quantize_hours(_to_decimal(raw_non_operational)) or _zero_decimal(),
        )
        for raw_date, raw_count, raw_output, raw_customer, raw_candidate, raw_non_operational in trend_result.all()
    ]

    async def load_breakdown(
        *group_columns: Any,
        order_by_candidate: bool = True,
        limit: int | None = 12,
    ) -> list[Any]:
        candidate_sum = func.coalesce(func.sum(ProjectTimesheetRecord.candidate_duration_hours), 0)
        statement = (
            _timesheet_analytics_select_from(
                select(
                    *group_columns,
                    func.count(ProjectTimesheetRecord.id),
                    func.coalesce(func.sum(ProjectTimesheetRecord.output_quantity), 0),
                    func.coalesce(func.sum(ProjectTimesheetRecord.customer_duration_hours), 0),
                    candidate_sum,
                    func.coalesce(func.sum(ProjectTimesheetRecord.non_operational_duration_hours), 0),
                )
            )
            .where(*conditions)
            .group_by(*group_columns)
            .order_by(candidate_sum.desc() if order_by_candidate else func.count(ProjectTimesheetRecord.id).desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await db.execute(statement)
        rows: list[Any] = list(result.all())
        return rows

    company_breakdown = [
        _build_timesheet_metric_item(
            key=f"company-{raw_company_id}",
            label=company_name,
            company_id=int(raw_company_id),
            company_name=str(company_name),
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_company_id,
            company_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(AdminCompany.id, AdminCompany.name, limit=12)
    ]

    project_breakdown = [
        _build_timesheet_metric_item(
            key=f"project-{raw_project_id}",
            label=project_name,
            company_id=int(raw_company_id),
            company_name=str(company_name),
            project_id=int(raw_project_id),
            project_name=str(project_name),
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_company_id,
            company_name,
            raw_project_id,
            project_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            AdminCompany.id, AdminCompany.name, AdminCompanyProject.id, AdminCompanyProject.name, limit=12
        )
    ]

    language_breakdown = [
        _build_timesheet_metric_item(
            key=f"language-{language_value or 'blank'}",
            label=language_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            language_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.language,
            limit=12,
        )
    ]

    work_type_breakdown = [
        _build_timesheet_metric_item(
            key=f"work-type-{work_type_value or 'blank'}",
            label=work_type_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            work_type_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.work_type,
            limit=12,
        )
    ]

    role_breakdown = [
        _build_timesheet_metric_item(
            key=f"role-{role_value or 'blank'}",
            label=role_value,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            role_value,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.role_name,
            limit=12,
        )
    ]

    person_ranking = [
        _build_timesheet_metric_item(
            key=f"user-{raw_user_id}",
            label=user_name,
            user_id=int(raw_user_id),
            user_email=user_email,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            raw_user_id,
            user_name,
            user_email,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.user_id,
            ProjectTimesheetRecord.user_name_snapshot,
            ProjectTimesheetRecord.user_email_snapshot,
            limit=10,
        )
    ]

    sub_project_ranking = [
        _build_timesheet_metric_item(
            key=f"sub-project-{sub_project_name or 'blank'}",
            label=sub_project_name,
            record_count=record_total,
            output_quantity=raw_output,
            customer_duration_hours=raw_customer,
            candidate_duration_hours=raw_candidate,
            non_operational_duration_hours=raw_non_operational,
        )
        for (
            sub_project_name,
            record_total,
            raw_output,
            raw_customer,
            raw_candidate,
            raw_non_operational,
        ) in await load_breakdown(
            ProjectTimesheetRecord.sub_project_name,
            limit=10,
        )
    ]

    filter_options = await _load_timesheet_filter_options(db=db, conditions=conditions)

    return ProjectTimesheetAnalyticsRead(
        company_id=company_id,
        company_name=scope_company_name,
        project_id=project_id,
        project_name=scope_project_name,
        start_date=start_date,
        end_date=end_date,
        summary=summary,
        trend=trend,
        company_breakdown=company_breakdown,
        project_breakdown=project_breakdown,
        language_breakdown=language_breakdown,
        work_type_breakdown=work_type_breakdown,
        role_breakdown=role_breakdown,
        person_ranking=person_ranking,
        sub_project_ranking=sub_project_ranking,
        filter_options=filter_options,
    ).model_dump()
