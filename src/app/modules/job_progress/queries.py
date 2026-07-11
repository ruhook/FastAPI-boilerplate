from collections import defaultdict
from typing import Any, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from ...core.advanced_filter import (
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.dictionary.service import get_dictionary_option_label_map_by_key
from ..assets.model import Asset
from ..assets.service import serialize_asset
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..contract_record.const import CONTRACT_STATUS_EXPIRED, CONTRACT_STATUS_TERMINATED
from ..contract_record.model import ContractRecord
from ..contract_record.queries import (
    get_current_contract_record_by_progress_id,
    list_current_contract_records_by_progress_ids,
)
from ..job.const import JOB_DATA_CONTRACT_EXAMPLE_KEY
from ..job.model import Job
from .candidate_presentation import CandidatePresentation
from .const import RecruitmentStage, get_recruitment_stage_cn_name
from .filtering import _build_progress_advanced_filter_field_map
from .model import JobProgress
from .normalization import _ensure_utc_datetime, _normalize_text
from .schema import (
    CandidateContractListItemRead,
    CandidateContractListPage,
    CandidateJobApplicationDetailRead,
    CandidateJobApplicationListItemRead,
    CandidateJobApplicationListPage,
    CandidateJobApplicationSummaryRead,
    ContractRecordDataRead,
    JobProgressListItemRead,
    JobProgressListPage,
)
from .serialization import (
    _build_candidate_compensation_label,
    _build_candidate_presentation_for_progress,
    _extract_contract_record_asset_ids,
    _extract_process_asset_ids,
    _get_candidate_visible_stage,
    _get_candidate_visible_stage_label,
    _list_id_attachment_asset_ids_by_user,
    _serialize_application_assets,
    _serialize_application_snapshot,
    _serialize_contract_record_data,
    _serialize_identity_attachment_asset,
    _serialize_process_assets,
    _serialize_process_data,
    _serialize_progress_process_data,
    _should_show_candidate_compensation,
)
from .state import (
    _get_company_name_map_by_company_ids,
    _get_company_name_map_by_job_ids,
    _get_project_name_map_by_project_ids,
)


async def list_job_progress(
    *,
    job_id: int,
    active_stage: str | None = None,
    advanced_filter: str | None = None,
    current_stages: list[str] | None = None,
    reviewer_admin_user_id: int | None = None,
    db: AsyncSession,
) -> dict[str, Any]:
    advanced_filter_query = parse_advanced_filter_query(advanced_filter)
    normalized_stages = [stage for stage in (current_stages or []) if stage]
    normalized_active_stage = _normalize_text(active_stage)
    if normalized_active_stage and normalized_active_stage not in {
        "all",
        "screening",
        "assessment",
        "passed",
        "contract",
        "employed",
        "replaced",
        "eliminated",
    }:
        raise BadRequestException("Unsupported active stage for advanced filter.")
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    company_name_map = await _get_company_name_map_by_job_ids(job_ids=[job_id], db=db)
    current_company_name = company_name_map.get(job_id)
    result = await db.execute(
        select(JobProgress, CandidateApplication)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
            *([JobProgress.current_stage.in_(normalized_stages)] if normalized_stages else []),
            *(
                [JobProgress.assessment_reviewer_admin_user_id == reviewer_admin_user_id]
                if reviewer_admin_user_id is not None
                else []
            ),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
    )
    rows = result.all()
    if not rows:
        return JobProgressListPage(items=[], total=0).model_dump()

    application_ids = [application.id for _, application in rows]
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id.in_(application_ids))
        .order_by(
            CandidateApplicationFieldValue.application_id.asc(),
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = field_result.scalars().all()
    grouped_field_rows: dict[int, list[CandidateApplicationFieldValue]] = defaultdict(list)
    for row in field_rows:
        grouped_field_rows[int(row.application_id)].append(row)

    asset_ids = {int(row.asset_id) for row in field_rows if row.asset_id is not None}
    contract_records = await list_current_contract_records_by_progress_ids(
        progress_ids=[progress.id for progress, _ in rows],
        db=db,
    )
    contract_company_name_map = await _get_company_name_map_by_company_ids(
        company_ids=[
            record.service_customer_company_id
            for record in contract_records.values()
            if record.service_customer_company_id is not None
        ],
        db=db,
    )
    contract_project_name_map = await _get_project_name_map_by_project_ids(
        project_ids=[
            record.service_customer_project_id
            for record in contract_records.values()
            if record.service_customer_project_id is not None
        ],
        db=db,
    )
    id_attachment_asset_ids_by_user = await _list_id_attachment_asset_ids_by_user(
        db=db,
        user_ids={int(progress.user_id) for progress, _ in rows},
    )
    for progress, _ in rows:
        asset_ids.update(_extract_process_asset_ids(progress.data or {}))
        asset_ids.update(_extract_contract_record_asset_ids(contract_records.get(progress.id)))
    asset_ids.update(id_attachment_asset_ids_by_user.values())
    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    items = [
        JobProgressListItemRead(
            id=progress.id,
            job_id=progress.job_id,
            user_id=progress.user_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            current_stage=progress.current_stage,
            version=progress.version,
            current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
            screening_mode=progress.screening_mode,
            applied_at=_ensure_utc_datetime(application.submitted_at),
            job_title=application.job_snapshot_title,
            job_company_name=current_company_name,
            application_snapshot=_serialize_application_snapshot(grouped_field_rows.get(application.id, [])),
            application_assets=_serialize_application_assets(grouped_field_rows.get(application.id, []), asset_map),
            process_data=_serialize_progress_process_data(
                progress.data or {},
                asset_map,
            ),
            process_assets=_serialize_process_assets(
                progress.data or {},
                asset_map,
                exclude_contract_assets=True,
            )
            | _serialize_identity_attachment_asset(
                user_id=progress.user_id,
                id_attachment_asset_ids_by_user=id_attachment_asset_ids_by_user,
                asset_map=asset_map,
            ),
            contract_record_data=_serialize_contract_record_data(
                progress=progress,
                contract_record=contract_records.get(progress.id),
                asset_map=asset_map,
                current_company_name=(
                    contract_company_name_map.get(
                        int(contract_records[progress.id].service_customer_company_id or 0)
                    )
                    if contract_records.get(progress.id) is not None
                    and contract_records[progress.id].service_customer_company_id is not None
                    else None
                ),
                current_project_name=(
                    contract_project_name_map.get(
                        int(contract_records[progress.id].service_customer_project_id or 0)
                    )
                    if contract_records.get(progress.id) is not None
                    and contract_records[progress.id].service_customer_project_id is not None
                    else None
                ),
            ),
        )
        for progress, application in rows
    ]
    matched_progress_ids: list[int] | None = None
    if has_advanced_filter_rules(advanced_filter_query):
        field_map = _build_progress_advanced_filter_field_map(job)
        validate_advanced_filter_query(advanced_filter_query, field_map=field_map)
        matched_conditions = [
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
            *([JobProgress.current_stage.in_(normalized_stages)] if normalized_stages else []),
            *(
                [JobProgress.assessment_reviewer_admin_user_id == reviewer_admin_user_id]
                if reviewer_admin_user_id is not None
                else []
            ),
        ]
        advanced_filter_condition = build_advanced_filter_query_sql_condition(
            advanced_filter_query,
            field_map=field_map,
        )
        if advanced_filter_condition is not None:
            matched_conditions.append(advanced_filter_condition)
        if normalized_active_stage not in {"", "all"}:
            stage_expression = cast(ColumnElement[Any], field_map["current_stage"].sql_expression)
            matched_conditions.append(stage_expression == normalized_active_stage)
        matched_result = await db.execute(
            select(JobProgress.id)
            .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
            .where(*matched_conditions)
            .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        )
        matched_progress_ids = [int(progress_id) for progress_id in matched_result.scalars().all()]

    return JobProgressListPage(
        items=items,
        total=len(items),
        matched_progress_ids=matched_progress_ids,
    ).model_dump()


async def list_candidate_job_applications(
    *,
    user_id: int,
    page: int,
    page_size: int,
    keyword: str | None = None,
    current_stage: str | None = None,
    needs_action_only: bool = False,
    db: AsyncSession,
) -> dict[str, Any]:
    conditions = [
        JobProgress.user_id == user_id,
        JobProgress.is_deleted.is_(False),
        CandidateApplication.is_deleted.is_(False),
        Job.is_deleted.is_(False),
    ]
    normalized_keyword = _normalize_text(keyword)
    if normalized_keyword:
        term = f"%{normalized_keyword}%"
        conditions.append(CandidateApplication.job_snapshot_title.ilike(term))
    normalized_stage = _normalize_text(current_stage)
    if normalized_stage:
        conditions.append(JobProgress.current_stage == normalized_stage)

    current_contract = aliased(ContractRecord)
    current_contract_ids = (
        select(
            ContractRecord.job_progress_id.label("job_progress_id"),
            func.max(ContractRecord.id).label("contract_record_id"),
        )
        .where(
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .group_by(ContractRecord.job_progress_id)
        .subquery()
    )
    result = await db.stream(
        select(JobProgress, CandidateApplication, Job, current_contract)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .outerjoin(current_contract_ids, current_contract_ids.c.job_progress_id == JobProgress.id)
        .outerjoin(
            current_contract,
            and_(
                current_contract.id == current_contract_ids.c.contract_record_id,
                current_contract.job_progress_id == JobProgress.id,
            ),
        )
        .where(*conditions)
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        .execution_options(yield_per=max(page_size, 100))
    )

    start = (page - 1) * page_size
    end = start + page_size
    total = 0
    contract_uploads = 0
    other_actions = 0
    rows: list[tuple[JobProgress, CandidateApplication, Job, CandidatePresentation]] = []
    contract_records: dict[int, ContractRecord] = {}
    async for progress, application, job, contract_record in result:
        presentation = _build_candidate_presentation_for_progress(
            progress=progress,
            job=job,
            contract_record=contract_record,
        )
        if needs_action_only and not presentation["candidate_action_required"]:
            continue

        row_index = total
        total += 1
        if presentation["candidate_action"] == "upload_contract":
            contract_uploads += 1
        elif presentation["candidate_action_required"]:
            other_actions += 1

        if start <= row_index < end:
            rows.append((progress, application, job, presentation))
            if contract_record is not None:
                contract_records[int(progress.id)] = contract_record

    total_action_required = contract_uploads + other_actions
    summary = CandidateJobApplicationSummaryRead(
        contract_uploads=contract_uploads,
        other_actions=other_actions,
        monitoring=total - total_action_required,
        total_action_required=total_action_required,
    )
    if not rows:
        return CandidateJobApplicationListPage(
            items=[],
            total=total,
            page=page,
            page_size=page_size,
            summary=summary,
        ).model_dump()

    application_ids = [application.id for _, application, _, _ in rows]
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id.in_(application_ids))
        .order_by(
            CandidateApplicationFieldValue.application_id.asc(),
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = field_result.scalars().all()
    grouped_field_rows: dict[int, list[CandidateApplicationFieldValue]] = defaultdict(list)
    for row in field_rows:
        grouped_field_rows[int(row.application_id)].append(row)

    asset_ids = {int(row.asset_id) for row in field_rows if row.asset_id is not None}
    for progress, _, _, _ in rows:
        asset_ids.update(_extract_process_asset_ids(progress.data or {}))
        asset_ids.update(_extract_contract_record_asset_ids(contract_records.get(progress.id)))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    country_label_map = await get_dictionary_option_label_map_by_key(key="country", db=db)

    items = [
        CandidateJobApplicationListItemRead(
            application_id=application.id,
            job_progress_id=progress.id,
            job_id=progress.job_id,
            job_title=application.job_snapshot_title,
            job_company_name=None,
            job_status=job.status,
            current_stage=progress.current_stage,
            current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
            candidate_visible_stage=(visible_stage := _get_candidate_visible_stage(progress, job)),
            candidate_visible_stage_label=_get_candidate_visible_stage_label(progress, visible_stage),
            screening_mode=progress.screening_mode,
            applied_at=_ensure_utc_datetime(application.submitted_at),
            country=job.country,
            country_label=country_label_map.get(job.country.strip()) if job.country.strip() else None,
            work_mode=job.work_mode,
            assessment_enabled=job.assessment_enabled,
            **presentation,
            application_snapshot=_serialize_application_snapshot(grouped_field_rows.get(application.id, [])),
            application_assets=_serialize_application_assets(grouped_field_rows.get(application.id, []), asset_map),
            process_data=_serialize_process_data(progress.data or {}, asset_map, exclude_contract_fields=True),
            process_assets=_serialize_process_assets(progress.data or {}, asset_map, exclude_contract_assets=True),
            contract_record_data=_serialize_contract_record_data(
                progress=progress,
                contract_record=contract_records.get(progress.id),
                asset_map=asset_map,
                current_company_name=None,
                current_project_name=None,
            ),
        )
        for progress, application, job, presentation in rows
    ]
    return CandidateJobApplicationListPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        summary=summary,
    ).model_dump()


async def list_candidate_contracts(
    *,
    user_id: int,
    page: int,
    page_size: int,
    keyword: str | None = None,
    db: AsyncSession,
) -> dict[str, Any]:
    conditions = [
        JobProgress.user_id == user_id,
        JobProgress.is_deleted.is_(False),
        CandidateApplication.is_deleted.is_(False),
        Job.is_deleted.is_(False),
        ContractRecord.is_deleted.is_(False),
        ContractRecord.is_current.is_(True),
        ContractRecord.contract_status.notin_(
            [
                CONTRACT_STATUS_TERMINATED,
                CONTRACT_STATUS_EXPIRED,
            ]
        ),
        JobProgress.current_stage.notin_(
            [
                RecruitmentStage.REJECTED.value,
                RecruitmentStage.REPLACED.value,
            ]
        ),
        or_(
            ContractRecord.company_sealed_contract_asset_id.is_not(None),
            ContractRecord.contract_attachment_asset_id.is_not(None),
        ),
    ]
    normalized_keyword = _normalize_text(keyword)
    if normalized_keyword:
        term = f"%{normalized_keyword}%"
        conditions.append(
            or_(
                CandidateApplication.job_snapshot_title.ilike(term),
                ContractRecord.agreement_ref_no.ilike(term),
                ContractRecord.contractor_name.ilike(term),
            )
        )

    total_result = await db.execute(
        select(func.count())
        .select_from(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .join(
            ContractRecord,
            (ContractRecord.job_progress_id == JobProgress.id)
            & ContractRecord.is_deleted.is_(False)
            & ContractRecord.is_current.is_(True),
        )
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)
    if total == 0:
        return CandidateContractListPage(items=[], total=0, page=page, page_size=page_size).model_dump()

    result = await db.execute(
        select(JobProgress, CandidateApplication, Job, ContractRecord)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .join(
            ContractRecord,
            (ContractRecord.job_progress_id == JobProgress.id)
            & ContractRecord.is_deleted.is_(False)
            & ContractRecord.is_current.is_(True),
        )
        .where(*conditions)
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()
    if not rows:
        return CandidateContractListPage(items=[], total=total, page=page, page_size=page_size).model_dump()

    asset_ids: set[int] = set()
    for _, _, _, contract_record in rows:
        asset_ids.update(_extract_contract_record_asset_ids(contract_record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    items = [
        CandidateContractListItemRead(
            application_id=application.id,
            job_progress_id=progress.id,
            job_id=job.id,
            job_title=application.job_snapshot_title,
            job_company_name=None,
            job_status=job.status,
            current_stage=progress.current_stage,
            current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
            applied_at=_ensure_utc_datetime(application.submitted_at),
            compensation_unit=job.compensation_unit,
            process_data=_serialize_process_data(progress.data or {}, asset_map, exclude_contract_fields=True),
            contract_record_data=cast(
                ContractRecordDataRead,
                _serialize_contract_record_data(
                    progress=progress,
                    contract_record=contract_record,
                    asset_map=asset_map,
                    current_company_name=None,
                    current_project_name=None,
                ),
            ),
        )
        for progress, application, job, contract_record in rows
    ]
    return CandidateContractListPage(items=items, total=total, page=page, page_size=page_size).model_dump()


async def get_candidate_job_application_detail(
    *,
    user_id: int,
    application_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    country_label_map = await get_dictionary_option_label_map_by_key(key="country", db=db)
    result = await db.execute(
        select(JobProgress, CandidateApplication, Job)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
        .where(
            JobProgress.user_id == user_id,
            JobProgress.application_id == application_id,
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
            Job.is_deleted.is_(False),
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundException("Application not found.")

    progress, application, job = row
    field_result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id == application.id)
        .order_by(
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
    )
    field_rows = list(field_result.scalars().all())

    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)

    asset_ids = {int(item.asset_id) for item in field_rows if item.asset_id is not None}
    asset_ids.update(_extract_process_asset_ids(progress.data or {}))
    asset_ids.update(_extract_contract_record_asset_ids(contract_record))
    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    visible_stage = _get_candidate_visible_stage(progress, job)
    presentation = _build_candidate_presentation_for_progress(
        progress=progress,
        job=job,
        contract_record=contract_record,
    )
    return CandidateJobApplicationDetailRead(
        application_id=application.id,
        job_progress_id=progress.id,
        job_id=job.id,
        job_title=application.job_snapshot_title,
        job_company_name=None,
        job_status=job.status,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        candidate_visible_stage=visible_stage,
        candidate_visible_stage_label=_get_candidate_visible_stage_label(progress, visible_stage),
        screening_mode=progress.screening_mode,
        applied_at=_ensure_utc_datetime(application.submitted_at),
        description_html=job.description,
        contract_example_html=str((job.data or {}).get(JOB_DATA_CONTRACT_EXAMPLE_KEY) or ""),
        country=job.country,
        country_label=country_label_map.get(job.country.strip()) if job.country.strip() else None,
        work_mode=job.work_mode,
        show_compensation=_should_show_candidate_compensation(job),
        compensation_unit=job.compensation_unit,
        compensation_label=(
            _build_candidate_compensation_label(job) if _should_show_candidate_compensation(job) else "-"
        ),
        assessment_enabled=job.assessment_enabled,
        **presentation,
        application_snapshot=_serialize_application_snapshot(field_rows),
        application_assets=_serialize_application_assets(field_rows, asset_map),
        process_data=_serialize_process_data(progress.data or {}, asset_map, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress.data or {}, asset_map, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=asset_map,
            current_company_name=None,
            current_project_name=None,
        ),
    ).model_dump()
