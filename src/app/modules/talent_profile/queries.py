from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.exceptions.http_exceptions import NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany
from ..assets.model import Asset
from ..candidate_application.const import get_candidate_application_status_cn_name
from ..candidate_application.model import CandidateApplication
from ..candidate_application.schema import CandidateApplicationSummaryRead
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..contract_record.model import ContractRecord
from ..job.model import Job
from ..job_progress.const import JobProgressDataKey, RecruitmentStage, get_recruitment_stage_cn_name
from ..job_progress.model import JobProgress
from ..operation_log.model import OperationLog
from ..operation_log.schema import OperationLogRead
from ..payment.model import Payment
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..referral.model import ReferralRecord
from .model import TalentProfile
from .pool_fields import build_talent_pool_extra_fields, load_talent_pool_sources
from .schema import (
    TalentPaymentRead,
    TalentPendingMergeFieldRead,
    TalentPendingMergeRead,
    TalentProfileListItemRead,
    TalentProfileListPage,
    TalentProfileRead,
    TalentTimesheetRecordRead,
)
from .serialization import (
    TALENT_ASSET_FIELD_MAPPING,
    TALENT_FIELD_MAPPING,
    _build_operation_log_summary,
    _get_operation_log_actor_type,
    _get_operation_log_status_label,
    _get_operation_log_title,
)


def _json_text_expression(column: Any, key: str):
    return func.json_unquote(func.json_extract(column, f"$.{key}"))


def _latest_progress_json_text_expression(key: str):
    return (
        select(_json_text_expression(JobProgress.data, key))
        .where(
            JobProgress.talent_profile_id == TalentProfile.id,
            JobProgress.is_deleted.is_(False),
        )
        .order_by(JobProgress.updated_at.desc(), JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _latest_progress_stage_expression():
    return (
        select(JobProgress.current_stage)
        .where(
            JobProgress.talent_profile_id == TalentProfile.id,
            JobProgress.is_deleted.is_(False),
        )
        .order_by(JobProgress.updated_at.desc(), JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _current_contract_field_expression(column: Any):
    return (
        select(column)
        .where(
            ContractRecord.talent_profile_id == TalentProfile.id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .order_by(ContractRecord.updated_at.desc(), ContractRecord.created_at.desc(), ContractRecord.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _talent_status_sql_expression():
    latest_stage = _latest_progress_stage_expression()
    latest_onboarding_date = _latest_progress_json_text_expression(JobProgressDataKey.ONBOARDING_DATE.value)
    return case(
        (latest_stage == RecruitmentStage.REJECTED.value, "rejected"),
        (latest_stage == RecruitmentStage.REPLACED.value, "replaced"),
        (
            or_(
                latest_onboarding_date.is_(None),
                func.trim(func.coalesce(latest_onboarding_date, "")) == "",
            ),
            "recruiting",
        ),
        else_=func.coalesce(TalentProfile.status_override, "active"),
    )


TALENT_ADVANCED_FILTER_FIELD_MAP: dict[str, AdvancedFilterFieldDefinition] = {
    "full_name": AdvancedFilterFieldDefinition(
        name="full_name",
        filter_kind="text",
        sql_expression=TalentProfile.full_name,
    ),
    "email": AdvancedFilterFieldDefinition(
        name="email",
        filter_kind="email",
        sql_expression=TalentProfile.email,
    ),
    "whatsapp": AdvancedFilterFieldDefinition(
        name="whatsapp",
        filter_kind="text",
        sql_expression=TalentProfile.whatsapp,
    ),
    "nationality": AdvancedFilterFieldDefinition(
        name="nationality",
        filter_kind="text",
        sql_expression=TalentProfile.nationality,
    ),
    "location": AdvancedFilterFieldDefinition(
        name="location",
        filter_kind="text",
        sql_expression=TalentProfile.location,
    ),
    "native_languages": AdvancedFilterFieldDefinition(
        name="native_languages",
        filter_kind="text",
        sql_expression=TalentProfile.native_languages,
    ),
    "additional_languages": AdvancedFilterFieldDefinition(
        name="additional_languages",
        filter_kind="text",
        sql_expression=TalentProfile.additional_languages,
    ),
    "education": AdvancedFilterFieldDefinition(
        name="education",
        filter_kind="text",
        sql_expression=TalentProfile.education,
    ),
    "latest_applied_job_title": AdvancedFilterFieldDefinition(
        name="latest_applied_job_title",
        filter_kind="text",
        sql_expression=TalentProfile.latest_applied_job_title,
    ),
    "latest_applied_job_id": AdvancedFilterFieldDefinition(
        name="latest_applied_job_id",
        filter_kind="number",
        sql_expression=TalentProfile.latest_applied_job_id,
    ),
    "resume_attachment": AdvancedFilterFieldDefinition(
        name="resume_attachment",
        filter_kind="file",
        sql_expression=TalentProfile.resume_asset_id,
    ),
    "note": AdvancedFilterFieldDefinition(
        name="note",
        filter_kind="text",
        sql_expression=TalentProfile.note,
    ),
    "merge_strategy": AdvancedFilterFieldDefinition(
        name="merge_strategy",
        filter_kind="select",
        sql_expression=TalentProfile.merge_strategy,
    ),
    "source_application_id": AdvancedFilterFieldDefinition(
        name="source_application_id",
        filter_kind="number",
        sql_expression=TalentProfile.source_application_id,
    ),
    "latest_applied_at": AdvancedFilterFieldDefinition(
        name="latest_applied_at",
        filter_kind="date",
        sql_expression=TalentProfile.latest_applied_at,
    ),
    "created_at": AdvancedFilterFieldDefinition(
        name="created_at",
        filter_kind="date",
        sql_expression=TalentProfile.created_at,
    ),
    "talent_status": AdvancedFilterFieldDefinition(
        name="talent_status",
        filter_kind="select",
        sql_expression=_talent_status_sql_expression(),
    ),
    "progress_language": AdvancedFilterFieldDefinition(
        name="progress_language",
        filter_kind="text",
        sql_expression=_latest_progress_json_text_expression(JobProgressDataKey.JOB_LANGUAGES.value),
    ),
    "contract_number": AdvancedFilterFieldDefinition(
        name="contract_number",
        filter_kind="text",
        sql_expression=func.coalesce(
            _current_contract_field_expression(ContractRecord.agreement_ref_no),
            _latest_progress_json_text_expression(JobProgressDataKey.CONTRACT_NUMBER.value),
        ),
    ),
    "contract_type": AdvancedFilterFieldDefinition(
        name="contract_type",
        filter_kind="select",
        sql_expression=_current_contract_field_expression(ContractRecord.contract_type),
    ),
    "onboarding_status": AdvancedFilterFieldDefinition(
        name="onboarding_status",
        filter_kind="text",
        sql_expression=_latest_progress_json_text_expression(JobProgressDataKey.ONBOARDING_STATUS.value),
    ),
}


async def _get_talent_profile_model(talent_id: int, db: AsyncSession) -> TalentProfile:
    result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.id == talent_id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = result.scalar_one_or_none()
    if talent is None:
        raise NotFoundException("Talent profile not found.")
    return talent


async def _get_talent_profile_model_by_user_id(user_id: int, db: AsyncSession) -> TalentProfile:
    result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == user_id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = result.scalar_one_or_none()
    if talent is None:
        raise NotFoundException("Talent profile not found.")
    return talent


async def _get_application_model(application_id: int, db: AsyncSession) -> CandidateApplication:
    result = await db.execute(
        select(CandidateApplication).where(
            CandidateApplication.id == application_id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise NotFoundException("Application not found.")
    return application


async def _list_application_field_rows(application_id: int, db: AsyncSession) -> list[CandidateApplicationFieldValue]:
    result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id == application_id)
        .order_by(CandidateApplicationFieldValue.sort_order.asc(), CandidateApplicationFieldValue.id.asc())
    )
    return list(result.scalars().all())


async def _serialize_talent_profile(talent: TalentProfile, db: AsyncSession) -> dict[str, Any]:
    asset_name: str | None = None
    if talent.resume_asset_id is not None:
        asset_result = await db.execute(
            select(Asset.original_name).where(
                Asset.id == talent.resume_asset_id,
                Asset.is_deleted.is_(False),
            )
        )
        asset_name = asset_result.scalar_one_or_none()
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    extra_fields = build_talent_pool_extra_fields(talent, source_bundle)

    applications_result = await db.execute(
        select(CandidateApplication)
        .where(
            CandidateApplication.user_id == talent.user_id,
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        .limit(20)
    )
    application_models = list(applications_result.scalars().all())
    job_company_name_map: dict[int, str | None] = {}
    if application_models:
        job_result = await db.execute(
            select(Job.id, AdminCompany.name)
            .outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
            .where(
                Job.id.in_([application.job_id for application in application_models]),
                Job.is_deleted.is_(False),
            )
        )
        job_company_name_map = {int(job_id): company_name for job_id, company_name in job_result.all()}
    application_ids = [application.id for application in application_models]
    progress_map: dict[int, JobProgress] = {}
    if application_ids:
        progress_result = await db.execute(
            select(JobProgress)
            .where(
                JobProgress.application_id.in_(application_ids),
                JobProgress.is_deleted.is_(False),
            )
            .order_by(JobProgress.id.desc())
        )
        for progress in progress_result.scalars().all():
            progress_map.setdefault(int(progress.application_id), progress)

    applications: list[CandidateApplicationSummaryRead] = []
    for application in application_models:
        current_progress = progress_map.get(int(application.id))
        applications.append(
            CandidateApplicationSummaryRead(
            id=application.id,
            job_id=application.job_id,
            job_snapshot_title=application.job_snapshot_title,
            job_company_name=job_company_name_map.get(application.job_id),
            status=application.status,
            status_cn_name=get_candidate_application_status_cn_name(application.status),
            current_stage=current_progress.current_stage if current_progress is not None else None,
            current_stage_cn_name=(
                get_recruitment_stage_cn_name(current_progress.current_stage)
                if current_progress is not None
                else None
            ),
            submitted_at=application.submitted_at,
            source_of_current_snapshot=application.id == talent.source_application_id,
            )
        )

    pending_merge = await _build_pending_merge_payload(
        talent=talent,
        db=db,
        applications=application_models,
        current_resume_asset_name=asset_name,
    )
    logs = await _list_talent_operation_logs(talent=talent, db=db)
    timesheet_records = await _list_talent_timesheet_records(talent=talent, db=db)
    payments = await _list_talent_payments(talent=talent, db=db)

    return TalentProfileRead(
        id=talent.id,
        user_id=talent.user_id,
        full_name=talent.full_name,
        email=talent.email,
        whatsapp=talent.whatsapp,
        nationality=talent.nationality,
        location=talent.location,
        native_languages=talent.native_languages,
        additional_languages=talent.additional_languages,
        education=talent.education,
        latest_applied_job_id=talent.latest_applied_job_id,
        latest_applied_job_title=talent.latest_applied_job_title,
        latest_applied_at=talent.latest_applied_at,
        resume_asset_id=talent.resume_asset_id,
        resume_asset_name=asset_name,
        note=extra_fields.pop("note", talent.note),
        merge_strategy=talent.merge_strategy,
        source_application_id=talent.source_application_id,
        created_at=talent.created_at,
        last_merged_at=talent.last_merged_at,
        applications=applications,
        timesheet_records=timesheet_records,
        payments=payments,
        pending_merge=pending_merge,
        logs=logs,
        **extra_fields,
    ).model_dump()


async def _list_talent_timesheet_records(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[TalentTimesheetRecordRead]:
    result = await db.execute(
        select(ProjectTimesheetRecord)
        .where(
            ProjectTimesheetRecord.is_deleted.is_(False),
            or_(
                ProjectTimesheetRecord.talent_profile_id == talent.id,
                ProjectTimesheetRecord.user_id == talent.user_id,
            ),
        )
        .order_by(ProjectTimesheetRecord.work_date.desc(), ProjectTimesheetRecord.id.desc())
        .limit(50)
    )
    return [
        TalentTimesheetRecordRead(
            id=record.id,
            work_date=record.work_date.isoformat(),
            sub_project_name=record.sub_project_name,
            language=record.language,
            work_type=record.work_type,
            candidate_duration_hours=record.candidate_duration_hours,
            output_quantity=record.output_quantity,
            role_name=record.role_name,
            poc_evaluation=record.poc_evaluation,
            extra_notes=record.extra_notes,
        )
        for record in result.scalars().all()
    ]


async def _list_talent_payments(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[TalentPaymentRead]:
    result = await db.execute(
        select(Payment)
        .where(
            or_(
                Payment.talent_profile_id == talent.id,
                Payment.user_id == talent.user_id,
            ),
        )
        .order_by(Payment.paid_at.desc(), Payment.id.desc())
        .limit(50)
    )
    return [
        TalentPaymentRead(
            id=record.id,
            paid_at=record.paid_at,
            payment_type=record.payment_type,
            amount=record.amount,
            currency=record.currency,
            project_name=record.project_snapshot_name,
            contract_ref_no=record.contract_snapshot_ref_no,
            external_transaction_no=record.external_transaction_no,
            remark=record.remark,
        )
        for record in result.scalars().all()
    ]


async def _list_talent_operation_logs(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[OperationLogRead]:
    result = await db.execute(
        select(OperationLog)
        .where(
            or_(
                OperationLog.talent_profile_id == talent.id,
                and_(OperationLog.talent_profile_id.is_(None), OperationLog.user_id == talent.user_id),
            )
        )
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
    )
    log_models = list(result.scalars().all())

    application_ids = sorted({log.application_id for log in log_models if log.application_id is not None})
    job_ids = sorted({log.job_id for log in log_models if log.job_id is not None})

    application_titles: dict[int, str] = {}
    if application_ids:
        application_result = await db.execute(
            select(CandidateApplication.id, CandidateApplication.job_snapshot_title).where(
                CandidateApplication.id.in_(application_ids)
            )
        )
        application_titles = {
            int(application_id): job_snapshot_title for application_id, job_snapshot_title in application_result.all()
        }

    job_titles: dict[int, str] = {}
    if job_ids:
        job_result = await db.execute(select(Job.id, Job.title).where(Job.id.in_(job_ids)))
        job_titles = {int(job_id): title for job_id, title in job_result.all()}

    admin_user_ids = sorted(
        {
            int(operator_admin_user_id)
            for log in log_models
            for operator_admin_user_id in [(log.data or {}).get("operator_admin_user_id")]
            if operator_admin_user_id is not None
        }
    )
    admin_user_labels: dict[int, str] = {}
    if admin_user_ids:
        admin_user_result = await db.execute(
            select(AdminUser.id, AdminUser.name, AdminUser.username).where(AdminUser.id.in_(admin_user_ids))
        )
        admin_user_labels = {
            int(admin_user_id): (name or username or str(admin_user_id))
            for admin_user_id, name, username in admin_user_result.all()
        }

    items: list[OperationLogRead] = []
    for log in log_models:
        job_title = None
        if log.application_id is not None:
            job_title = application_titles.get(log.application_id)
        if job_title is None and log.job_id is not None:
            job_title = job_titles.get(log.job_id)
        if job_title is None:
            raw_job_title = (log.data or {}).get("job_title")
            job_title = str(raw_job_title) if raw_job_title else None

        operator_admin_user_id = (log.data or {}).get("operator_admin_user_id")
        actor_name = None
        if operator_admin_user_id is not None:
            try:
                actor_name = admin_user_labels.get(int(operator_admin_user_id))
            except (TypeError, ValueError):
                actor_name = None

        items.append(
            OperationLogRead(
                id=log.id,
                user_id=log.user_id,
                job_id=log.job_id,
                job_title=job_title,
                application_id=log.application_id,
                talent_profile_id=log.talent_profile_id,
                log_type=log.log_type,
                title=_get_operation_log_title(log.log_type),
                summary=_build_operation_log_summary(log, job_title),
                actor_type=_get_operation_log_actor_type(log.log_type),
                actor_name=actor_name,
                status_label=_get_operation_log_status_label(log),
                data=log.data or {},
                created_at=log.created_at,
            )
        )
    return items


async def _build_pending_merge_payload(
    *,
    talent: TalentProfile,
    db: AsyncSession,
    applications: Sequence[CandidateApplication],
    current_resume_asset_name: str | None,
) -> TalentPendingMergeRead | None:
    latest_application = applications[0] if applications else None
    if latest_application is None:
        return None
    if latest_application.id == talent.source_application_id:
        return None
    if talent.last_merged_at and latest_application.submitted_at <= talent.last_merged_at:
        return None

    field_rows = await _list_application_field_rows(latest_application.id, db)
    incoming_asset_ids = [
        row.asset_id
        for row in field_rows
        if row.asset_id is not None and (row.catalog_key or row.field_key) in TALENT_ASSET_FIELD_MAPPING
    ]
    incoming_asset_names: dict[int, str] = {}
    if incoming_asset_ids:
        asset_result = await db.execute(
            select(Asset.id, Asset.original_name).where(
                Asset.id.in_(incoming_asset_ids),
                Asset.is_deleted.is_(False),
            )
        )
        incoming_asset_names = {int(asset_id): original_name for asset_id, original_name in asset_result.all()}

    field_diffs: list[TalentPendingMergeFieldRead] = []
    for row in field_rows:
        catalog_key = row.catalog_key or row.field_key
        if catalog_key in TALENT_FIELD_MAPPING:
            current_value = getattr(talent, TALENT_FIELD_MAPPING[catalog_key], None)
            incoming_value = row.display_value or row.raw_value
        elif catalog_key in TALENT_ASSET_FIELD_MAPPING:
            current_value = current_resume_asset_name
            incoming_value = incoming_asset_names.get(row.asset_id or 0) or row.display_value or row.raw_value
        else:
            continue

        normalized_current = (current_value or "").strip()
        normalized_incoming = (incoming_value or "").strip()
        if normalized_current == normalized_incoming:
            continue

        field_diffs.append(
            TalentPendingMergeFieldRead(
                key=catalog_key,
                label=row.field_label,
                current_value=current_value,
                incoming_value=incoming_value,
            )
        )

    if not field_diffs:
        return None

    return TalentPendingMergeRead(
        application_id=latest_application.id,
        submitted_at=latest_application.submitted_at,
        fields=field_diffs,
    )


async def get_talent_profile(talent_id: int, db: AsyncSession) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    return await _serialize_talent_profile(talent, db)


async def get_talent_profile_by_user_id(user_id: int, db: AsyncSession) -> dict[str, Any]:
    talent = await _get_talent_profile_model_by_user_id(user_id, db)
    return await _serialize_talent_profile(talent, db)


async def list_talent_profiles(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    company_id: int | None = None,
    project_id: int | None = None,
    advanced_filter: str | None = None,
) -> dict[str, Any]:
    advanced_filter_query = parse_advanced_filter_query(advanced_filter)
    if has_advanced_filter_rules(advanced_filter_query):
        validate_advanced_filter_query(advanced_filter_query, field_map=TALENT_ADVANCED_FILTER_FIELD_MAP)
    conditions: list[Any] = [TalentProfile.is_deleted.is_(False)]
    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                TalentProfile.full_name.ilike(term),
                TalentProfile.email.ilike(term),
                TalentProfile.whatsapp.ilike(term),
                TalentProfile.nationality.ilike(term),
                TalentProfile.location.ilike(term),
                TalentProfile.native_languages.ilike(term),
                TalentProfile.additional_languages.ilike(term),
                TalentProfile.education.ilike(term),
                TalentProfile.latest_applied_job_title.ilike(term),
                TalentProfile.note.ilike(term),
                select(ReferralRecord.id)
                .where(
                    ReferralRecord.referred_user_id == TalentProfile.user_id,
                    ReferralRecord.is_deleted.is_(False),
                    or_(
                        ReferralRecord.referrer_snapshot_name.ilike(term),
                        ReferralRecord.referrer_snapshot_email.ilike(term),
                    ),
                )
                .exists(),
                select(JobProgress.id)
                .where(
                    JobProgress.talent_profile_id == TalentProfile.id,
                    JobProgress.is_deleted.is_(False),
                    or_(
                        _json_text_expression(JobProgress.data, JobProgressDataKey.JOB_LANGUAGES.value).ilike(term),
                        _json_text_expression(JobProgress.data, JobProgressDataKey.ONBOARDING_STATUS.value).ilike(term),
                        _json_text_expression(JobProgress.data, JobProgressDataKey.CONTRACT_NUMBER.value).ilike(term),
                        _json_text_expression(JobProgress.data, JobProgressDataKey.NOTE.value).ilike(term),
                    ),
                )
                .exists(),
                select(ContractRecord.id)
                .where(
                    ContractRecord.talent_profile_id == TalentProfile.id,
                    ContractRecord.is_deleted.is_(False),
                    ContractRecord.is_current.is_(True),
                    or_(
                        ContractRecord.agreement_ref_no.ilike(term),
                        ContractRecord.contract_type.ilike(term),
                    ),
                )
                .exists(),
            )
        )

    if company_id is not None or project_id is not None:
        application_conditions: list[Any] = [
            CandidateApplication.user_id == TalentProfile.user_id,
            CandidateApplication.is_deleted.is_(False),
            Job.id == CandidateApplication.job_id,
            Job.is_deleted.is_(False),
        ]
        if company_id is not None:
            application_conditions.append(Job.company_id == company_id)
        if project_id is not None:
            application_conditions.append(Job.project_id == project_id)

        conditions.append(
            select(CandidateApplication.id)
            .join(Job, Job.id == CandidateApplication.job_id)
            .where(*application_conditions)
            .exists()
        )

    advanced_filter_condition = build_advanced_filter_query_sql_condition(
        advanced_filter_query,
        field_map=TALENT_ADVANCED_FILTER_FIELD_MAP,
    )
    if advanced_filter_condition is not None:
        conditions.append(advanced_filter_condition)

    base_query = (
        select(TalentProfile, Asset.original_name)
        .outerjoin(Asset, Asset.id == TalentProfile.resume_asset_id)
        .where(*conditions)
        .order_by(
            TalentProfile.latest_applied_at.is_(None).asc(),
            TalentProfile.latest_applied_at.desc(),
            TalentProfile.created_at.desc(),
            TalentProfile.id.desc(),
        )
    )

    total_result = await db.execute(select(func.count()).select_from(TalentProfile).where(*conditions))
    total = int(total_result.scalar() or 0)
    paged_result = await db.execute(base_query.offset((page - 1) * page_size).limit(page_size))
    talent_rows = list(paged_result.all())
    source_bundle = await load_talent_pool_sources(
        db=db,
        talents=[talent for talent, _asset_name in talent_rows],
    )
    paged_items = [
        TalentProfileListItemRead(
            id=talent.id,
            user_id=talent.user_id,
            full_name=talent.full_name,
            email=talent.email,
            whatsapp=talent.whatsapp,
            nationality=talent.nationality,
            location=talent.location,
            native_languages=talent.native_languages,
            additional_languages=talent.additional_languages,
            education=talent.education,
            latest_applied_job_id=talent.latest_applied_job_id,
            latest_applied_job_title=talent.latest_applied_job_title,
            resume_asset_id=talent.resume_asset_id,
            resume_asset_name=asset_name,
            note=(extra_fields := build_talent_pool_extra_fields(talent, source_bundle)).pop("note", talent.note),
            latest_applied_at=talent.latest_applied_at,
            created_at=talent.created_at,
            merge_strategy=talent.merge_strategy,
            source_application_id=talent.source_application_id,
            **extra_fields,
        )
        for talent, asset_name in talent_rows
    ]

    return TalentProfileListPage(
        items=paged_items,
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()
