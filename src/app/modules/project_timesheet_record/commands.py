from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ...application.settlement import sync_timesheet_change
from ...core.exceptions.http_exceptions import BadRequestException, ConflictException, NotFoundException
from ..contract_record.const import CONTRACT_STATUS_ACTIVE
from ..payable.source_policy import ensure_timesheets_editable
from .idempotency import claim_timesheet_request, complete_timesheet_request
from .model import ProjectTimesheetRecord
from .queries import (
    _get_company_and_project,
    _get_company_timesheet_languages,
    _get_company_timesheet_roles,
    _get_company_timesheet_work_types,
    _load_admin_user_payload_map,
    _load_note_asset_payload_map,
    _load_team_leader_payload_map,
    _resolve_project_manager_admin_user,
    _resolve_timesheet_worker,
    _validate_timesheet_note_assets,
    list_active_project_team_leaders,
    list_active_project_workers,
)
from .schema import (
    ProjectTimesheetBatchCreateRequest,
    ProjectTimesheetBatchCreateResponse,
    ProjectTimesheetBatchDeleteRequest,
    ProjectTimesheetBatchDeleteResponse,
    ProjectTimesheetUpdateRequest,
)
from .serialization import (
    _get_timesheet_worker_name,
    _quantize_candidate_duration_hours,
    _quantize_customer_duration_hours,
    _quantize_hours,
    _serialize_timesheet_record,
)


async def create_project_timesheet_records(
    *,
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchCreateRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    company, project = await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)
    claim = await claim_timesheet_request(
        db=db,
        company_id=company_id,
        project_id=project_id,
        admin_user_id=admin_user_id,
        payload=payload,
    )
    if claim.replayed_record_ids is not None:
        return ProjectTimesheetBatchCreateResponse(
            created_count=len(claim.replayed_record_ids),
            record_ids=claim.replayed_record_ids,
        ).model_dump()

    timesheet_languages = _get_company_timesheet_languages(company)
    if timesheet_languages and payload.language not in timesheet_languages:
        raise BadRequestException("Selected language is not configured for this company.")

    timesheet_work_types = set(_get_company_timesheet_work_types(company))
    timesheet_roles = set(_get_company_timesheet_roles(company))
    worker_options = await list_active_project_workers(company_id=company_id, project_id=project_id, db=db)
    worker_map = {
        int(worker["contract_record_id"]): worker for worker in worker_options if worker.get("contract_record_id")
    }
    team_leader_options = await list_active_project_team_leaders(company_id=company_id, project_id=project_id, db=db)
    active_team_leader_user_ids = {int(worker["user_id"]) for worker in team_leader_options}
    if int(payload.team_leader_user_id) > 0 and int(payload.team_leader_user_id) not in active_team_leader_user_ids:
        raise BadRequestException("Selected team leader must have an active contract.")
    project_manager = await _resolve_project_manager_admin_user(
        db=db,
        admin_user_id=int(payload.project_manager_admin_user_id),
    )

    note_asset_ids = sorted(
        {int(asset_id) for entry in payload.entries for asset_id in entry.note_asset_ids if int(asset_id) > 0}
    )
    await _validate_timesheet_note_assets(
        db=db,
        note_asset_ids=note_asset_ids,
        admin_user_id=admin_user_id,
    )

    created_records: list[ProjectTimesheetRecord] = []
    for entry in payload.entries:
        worker = worker_map.get(int(entry.contract_record_id))
        if worker is None:
            raise BadRequestException("Selected worker must have an active contract.")
        if entry.user_id is not None and int(entry.user_id) != int(worker["user_id"]):
            raise BadRequestException("Selected worker does not match the selected contract.")
        if timesheet_work_types and entry.work_type not in timesheet_work_types:
            raise BadRequestException("Selected work type is not configured for this company.")
        role_name = (entry.role_name or "").strip() or None
        if timesheet_roles and role_name and role_name not in timesheet_roles:
            raise BadRequestException("Selected role is not configured for this company.")

        record = ProjectTimesheetRecord(
            company_id=company.id,
            project_id=project.id,
            sub_project_name=payload.sub_project_name,
            work_date=entry.work_date,
            user_id=int(worker["user_id"]),
            talent_profile_id=worker.get("talent_profile_id"),
            contract_record_id=int(worker["contract_record_id"]),
            user_name_snapshot=str(worker["name"]),
            user_email_snapshot=worker.get("email"),
            project_manager_admin_user_id=int(project_manager.id),
            project_manager_name_snapshot=project_manager.name,
            language=payload.language,
            work_type=entry.work_type,
            output_quantity=_quantize_hours(entry.output_quantity),
            customer_human_efficiency_minutes=_quantize_hours(payload.customer_human_efficiency_minutes),
            candidate_human_efficiency_minutes=_quantize_hours(payload.candidate_human_efficiency_minutes),
            customer_duration_hours=_quantize_customer_duration_hours(entry.customer_duration_hours),
            candidate_duration_hours=_quantize_candidate_duration_hours(entry.candidate_duration_hours),
            role_name=role_name,
            non_operational_duration_hours=_quantize_hours(entry.non_operational_duration_hours),
            project_link=payload.project_link,
            poc_evaluation=(entry.poc_evaluation or "").strip() or None,
            extra_notes=(entry.extra_notes or "").strip() or None,
            created_by_admin_user_id=admin_user_id,
            updated_by_admin_user_id=admin_user_id,
            data={"note_asset_ids": list(entry.note_asset_ids)},
        )
        record.team_leader_user_id = int(payload.team_leader_user_id) or None
        db.add(record)
        created_records.append(record)

    await db.flush()
    record_ids = [record.id for record in created_records]
    await complete_timesheet_request(db=db, request=claim.request, record_ids=record_ids)
    for settlement_month in sorted({record.work_date.strftime("%Y-%m") for record in created_records}):
        await sync_timesheet_change(
            db=db,
            settlement_month=settlement_month,
            affected_user_ids=[record.user_id for record in created_records],
        )
    return ProjectTimesheetBatchCreateResponse(
        created_count=len(created_records),
        record_ids=record_ids,
    ).model_dump()


async def flush_timesheet_write(db: AsyncSession) -> None:
    try:
        await db.flush()
    except StaleDataError as exc:
        raise ConflictException("Timesheet record was changed by another request.") from exc


async def update_project_timesheet_record(
    *,
    company_id: int,
    project_id: int,
    record_id: int,
    payload: ProjectTimesheetUpdateRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    company, project = await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)
    result = await db.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.id == record_id,
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundException("Timesheet record not found.")
    if record.version != payload.version:
        raise ConflictException("Timesheet record was changed by another request.")
    await ensure_timesheets_editable(db, [record.id])
    previous_settlement_month = record.work_date.strftime("%Y-%m")
    previous_user_id = int(record.user_id)

    timesheet_languages = _get_company_timesheet_languages(company)
    if timesheet_languages and payload.language not in timesheet_languages:
        raise BadRequestException("Selected language is not configured for this company.")
    timesheet_work_types = set(_get_company_timesheet_work_types(company))
    if timesheet_work_types and payload.work_type not in timesheet_work_types:
        raise BadRequestException("Selected work type is not configured for this company.")
    role_name = (payload.role_name or "").strip() or None
    timesheet_roles = set(_get_company_timesheet_roles(company))
    if timesheet_roles and role_name and role_name not in timesheet_roles:
        raise BadRequestException("Selected role is not configured for this company.")
    team_leader_options = await list_active_project_team_leaders(company_id=company_id, project_id=project_id, db=db)
    active_team_leader_user_ids = {int(worker["user_id"]) for worker in team_leader_options}
    if int(payload.team_leader_user_id) > 0 and int(payload.team_leader_user_id) not in active_team_leader_user_ids:
        raise BadRequestException("Selected team leader must have an active contract.")
    await _validate_timesheet_note_assets(
        db=db,
        note_asset_ids=payload.note_asset_ids,
        admin_user_id=admin_user_id,
    )
    contract_record, user, talent = await _resolve_timesheet_worker(
        db=db,
        company_id=company.id,
        project_id=project.id,
        contract_record_id=int(payload.contract_record_id),
    )
    if (
        int(payload.contract_record_id) != int(record.contract_record_id or 0)
        and contract_record.contract_status != CONTRACT_STATUS_ACTIVE
    ):
        raise BadRequestException("Selected worker must have an active contract.")
    if payload.user_id is not None and int(payload.user_id) != int(user.id):
        raise BadRequestException("Selected worker does not match the selected contract.")

    record.sub_project_name = payload.sub_project_name
    record.work_date = payload.work_date
    record.user_id = int(user.id)
    record.talent_profile_id = int(talent.id) if talent is not None else contract_record.talent_profile_id
    record.contract_record_id = int(contract_record.id)
    record.user_name_snapshot = _get_timesheet_worker_name(user, talent)
    record.user_email_snapshot = contract_record.user_snapshot_email or user.email
    record.team_leader_user_id = int(payload.team_leader_user_id) or None
    record.language = payload.language
    record.work_type = payload.work_type
    record.output_quantity = _quantize_hours(payload.output_quantity)
    record.customer_human_efficiency_minutes = _quantize_hours(payload.customer_human_efficiency_minutes)
    record.candidate_human_efficiency_minutes = _quantize_hours(payload.candidate_human_efficiency_minutes)
    record.customer_duration_hours = _quantize_customer_duration_hours(payload.customer_duration_hours)
    record.candidate_duration_hours = _quantize_candidate_duration_hours(payload.candidate_duration_hours)
    record.role_name = role_name
    record.non_operational_duration_hours = _quantize_hours(payload.non_operational_duration_hours)
    record.project_link = payload.project_link
    record.poc_evaluation = (payload.poc_evaluation or "").strip() or None
    record.extra_notes = (payload.extra_notes or "").strip() or None
    record.updated_by_admin_user_id = admin_user_id
    record.updated_at = datetime.now(UTC)
    record.data = {**(record.data or {}), "note_asset_ids": list(payload.note_asset_ids)}

    await flush_timesheet_write(db)
    for settlement_month in sorted({previous_settlement_month, record.work_date.strftime("%Y-%m")}):
        await sync_timesheet_change(
            db=db,
            settlement_month=settlement_month,
            affected_user_ids=[previous_user_id, int(record.user_id)],
        )
    await db.refresh(record)
    asset_map = await _load_note_asset_payload_map(
        db=db,
        asset_ids=[int(asset_id) for asset_id in payload.note_asset_ids],
    )
    team_leader_map = await _load_team_leader_payload_map(
        db=db,
        user_ids=[int(record.team_leader_user_id)] if record.team_leader_user_id else [],
    )
    admin_user_map = await _load_admin_user_payload_map(
        db=db,
        admin_user_ids=[int(record.created_by_admin_user_id)] if record.created_by_admin_user_id else [],
    )
    return _serialize_timesheet_record(
        record,
        asset_map=asset_map,
        team_leader_map=team_leader_map,
        admin_user_map=admin_user_map,
    ).model_dump()


async def delete_project_timesheet_records(
    *,
    company_id: int,
    project_id: int,
    payload: ProjectTimesheetBatchDeleteRequest,
    db: AsyncSession,
    admin_user_id: int,
) -> dict[str, Any]:
    await _get_company_and_project(company_id=company_id, project_id=project_id, db=db)
    result = await db.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.id.in_(payload.record_ids),
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    records = result.scalars().all()
    await ensure_timesheets_editable(db, [record.id for record in records])
    settlement_months = sorted({record.work_date.strftime("%Y-%m") for record in records})
    affected_user_ids = [int(record.user_id) for record in records]
    now = datetime.now(UTC)
    for record in records:
        record.is_deleted = True
        record.deleted_at = now
        record.updated_at = now
        record.updated_by_admin_user_id = admin_user_id

    await flush_timesheet_write(db)
    for settlement_month in settlement_months:
        await sync_timesheet_change(
            db=db,
            settlement_month=settlement_month,
            affected_user_ids=affected_user_ids,
        )
    return ProjectTimesheetBatchDeleteResponse(deleted_count=len(records)).model_dump()
