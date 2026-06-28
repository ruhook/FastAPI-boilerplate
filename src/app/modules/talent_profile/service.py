import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany
from ..assets.model import Asset
from ..assets.service import ensure_assets_belong_to_owner
from ..candidate_application.const import get_candidate_application_status_cn_name
from ..candidate_application.model import CandidateApplication
from ..candidate_application.schema import (
    CandidateApplicationSubmitRequest,
    CandidateApplicationSubmitResponse,
    CandidateApplicationSummaryRead,
)
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_field.const import CandidateFieldKey
from ..candidate_field.service import hydrate_candidate_field_options
from ..job.const import JOB_DATA_APPLICATION_SUMMARY_KEY, JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..job.model import Job
from ..job_progress.const import JobProgressDataKey, RecruitmentStage, get_recruitment_stage_cn_name
from ..job_progress.model import JobProgress
from ..job_progress.service import create_job_progress_for_application
from ..operation_log.const import OperationLogType
from ..operation_log.model import OperationLog
from ..operation_log.schema import OperationLogRead
from ..operation_log.service import create_operation_log
from ..payment_record.model import PaymentRecord
from ..project_timesheet_record.model import ProjectTimesheetRecord
from ..talent_profile_merge_log.model import TalentProfileMergeLog
from .const import TalentMergeStrategy
from .model import TalentProfile
from .pool_fields import (
    TALENT_STATUS_OVERRIDE_KEY,
    TALENT_STATUS_REPLACED,
    build_talent_pool_extra_fields,
    load_talent_pool_sources,
    validate_manual_talent_status,
)
from .schema import (
    TalentPaymentRecordRead,
    TalentPendingMergeFieldRead,
    TalentPendingMergeRead,
    TalentProfileListItemRead,
    TalentProfileListPage,
    TalentProfileRead,
    TalentTimesheetRecordRead,
)

TALENT_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.FULL_NAME.value: "full_name",
    CandidateFieldKey.EMAIL.value: "email",
    CandidateFieldKey.WHATSAPP.value: "whatsapp",
    CandidateFieldKey.NATIONALITY.value: "nationality",
    CandidateFieldKey.COUNTRY_OF_RESIDENCE.value: "location",
    CandidateFieldKey.NATIVE_LANGUAGES.value: "native_languages",
    CandidateFieldKey.ADDITIONAL_LANGUAGES.value: "additional_languages",
    CandidateFieldKey.EDUCATION_STATUS.value: "education",
}

TALENT_ASSET_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.RESUME_ATTACHMENT.value: "resume_asset_id",
}

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
}


def _normalize_display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        flattened = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(flattened) if flattened else None
    return str(value)


def _get_operation_log_title(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "候选人提交报名"
    if log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        return "首次自动创建人才快照"
    if log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return "更新最近申请岗位"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "手动合并人才快照"
    if log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        return "创建岗位流程记录"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "岗位流程阶段变更"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        return "提交测试题附件"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "提交人选签回合同"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        return "更新测试题评审"
    if log_type == OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value:
        return "更新质检结果"
    if log_type == OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value:
        return "更新合同信息"
    if log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        return "上传待签合同"
    if log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        return "上传公司签回合同"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value:
        return "自动邮件任务已创建"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED.value:
        return "自动邮件已跳过"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED.value:
        return "自动邮件创建失败"
    if log_type == OperationLogType.REFERRAL_CREATED.value:
        return "创建邀请关系"
    if log_type == OperationLogType.REFERRAL_REWARD_MARKED_PAID.value:
        return "邀请奖励已发放"
    return log_type


def _get_operation_log_actor_type(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "admin"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "system"
    if log_type in {
        OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
        OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value,
        OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
        OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
    }:
        return "admin"
    return "system"


def _get_operation_log_status_label(log: OperationLog) -> str | None:
    data = log.data or {}
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        value = data.get("to_stage_cn_name") or data.get("to_stage")
        return str(value) if value else None
    value = data.get("current_stage_cn_name") or data.get("current_stage")
    return str(value) if value else None


def _build_operation_log_summary(log: OperationLog, job_title: str | None) -> str:
    data = log.data or {}
    resolved_job_title = job_title or data.get("job_title") or "-"

    if log.log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        count = data.get("submitted_items_count")
        return f"提交了 {resolved_job_title} 的报名表（字段数 {count or 0}）"
    if log.log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        application_id = log.application_id or data.get("application_id") or "-"
        return f"系统根据申请 #{application_id} 自动创建人才快照，合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return f"最近申请岗位更新为 {resolved_job_title}"
    if log.log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        application_id = log.application_id or data.get("application_id") or "-"
        return f"从申请 #{application_id} 手动合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        stage_cn_name = data.get("current_stage_cn_name") or data.get("current_stage") or "-"
        return f"{resolved_job_title} 已创建流程记录，初始阶段为 {stage_cn_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        from_stage = data.get("from_stage_cn_name") or data.get("from_stage") or "-"
        to_stage = data.get("to_stage_cn_name") or data.get("to_stage") or "-"
        return f"{resolved_job_title} 从 {from_stage} 流转到 {to_stage}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        attachment_name = data.get("assessment_attachment") or "-"
        return f"{resolved_job_title} 已提交测试题附件：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        attachment_name = data.get("submitted_contract_attachment") or "-"
        return f"{resolved_job_title} 已提交人选签回合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        updated_fields = data.get("updated_fields") or {}
        field_count = len(updated_fields) if isinstance(updated_fields, dict) else 0
        return f"{resolved_job_title} 已更新测试题评审信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value:
        updated_fields = data.get("updated_fields") or {}
        field_count = len(updated_fields) if isinstance(updated_fields, dict) else 0
        return f"{resolved_job_title} 已更新质检信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value:
        updated_fields = data.get("contract_updated_fields") or []
        field_count = len(updated_fields) if isinstance(updated_fields, list) else 0
        return f"{resolved_job_title} 已更新合同信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        attachment_name = data.get("contract_draft_attachment") or "-"
        return f"{resolved_job_title} 已上传待签合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        attachment_name = data.get("company_sealed_contract_attachment") or "-"
        return f"{resolved_job_title} 已上传公司签回合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        return f"{resolved_job_title} 已创建自动邮件任务（目标阶段：{target_stage}）"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        reason = data.get("reason") or "-"
        return f"{resolved_job_title} 自动邮件已跳过（目标阶段：{target_stage}，原因：{reason}）"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        reason = data.get("reason") or "-"
        return f"{resolved_job_title} 自动邮件创建失败（目标阶段：{target_stage}，原因：{reason}）"
    if log.log_type == OperationLogType.REFERRAL_CREATED.value:
        referrer_email = data.get("referrer_email") or "-"
        return f"通过邀请链接建立推荐关系，邀请者邮箱：{referrer_email}"
    if log.log_type == OperationLogType.REFERRAL_REWARD_MARKED_PAID.value:
        paid_amount = data.get("paid_reward_amount") or "-"
        return f"邀请奖励已标记发放，薪资记录节点已预留：USD {paid_amount}"
    return json.dumps(data, ensure_ascii=False) if data else "-"


def _serialize_raw_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _is_blank_application_value(value: Any, display_value: str | None) -> bool:
    if value is None:
        return not (display_value or "").strip()
    if isinstance(value, str):
        return not value.strip() and not (display_value or "").strip()
    if isinstance(value, list):
        return not [item for item in value if str(item).strip()]
    return False


def _normalize_option_values(raw_options: Any) -> set[str]:
    if not isinstance(raw_options, list):
        return set()
    normalized: set[str] = set()
    for option in raw_options:
        if isinstance(option, dict):
            for key in ("value", "label"):
                value = str(option.get(key) or "").strip()
                if value:
                    normalized.add(value)
            continue
        value = str(option or "").strip()
        if value:
            normalized.add(value)
    return normalized


def _normalize_submitted_option_values(value: Any, display_value: str | None) -> list[str]:
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
    elif value is None:
        values = []
    else:
        normalized = str(value).strip()
        values = [normalized] if normalized else []
    if not values and display_value:
        values = [item.strip() for item in display_value.split(",") if item.strip()]
    return values


async def _validate_application_items(
    *,
    job: Job,
    payload: CandidateApplicationSubmitRequest,
    current_user: dict[str, Any],
    db: AsyncSession,
) -> tuple[dict[str, dict[str, Any]], list[Any]]:
    raw_fields = [
        dict(field)
        for field in list((job.data or {}).get(JOB_DATA_FORM_FIELDS_KEY) or [])
        if isinstance(field, dict) and field.get("key")
    ]
    hydrated_fields = [
        field
        for field in await hydrate_candidate_field_options(raw_fields, db=db)
        if field.get("visible", True) is not False
    ]
    field_snapshot_map = {
        str(field.get("key")): dict(field)
        for field in hydrated_fields
        if isinstance(field, dict) and field.get("key")
    }
    submitted_keys: set[str] = set()
    asset_ids: list[int] = []

    for item in payload.items:
        field_key = str(item.field_key)
        if field_key == CandidateFieldKey.FULL_NAME.value:
            account_name = str(current_user.get("name") or current_user.get("email") or "").strip()
            item.value = account_name
            item.display_value = account_name
        elif field_key == CandidateFieldKey.EMAIL.value:
            account_email = str(current_user.get("email") or "").strip()
            item.value = account_email
            item.display_value = account_email
        if field_key in submitted_keys:
            raise BadRequestException(f"Duplicate application field: {field_key}.")
        submitted_keys.add(field_key)
        snapshot = field_snapshot_map.get(field_key)
        if snapshot is None:
            raise BadRequestException(f"Unsupported application field: {field_key}.")

        field_type = str(snapshot.get("type") or "text").strip().lower()
        is_file_field = field_type == "file"
        is_blank = _is_blank_application_value(item.value, item.display_value)

        if bool(snapshot.get("required")):
            if is_file_field:
                if item.asset_id in (None, 0, ""):
                    raise BadRequestException(f"{snapshot.get('label') or field_key} is required.")
            elif is_blank:
                raise BadRequestException(f"{snapshot.get('label') or field_key} is required.")

        if item.asset_id not in (None, 0, ""):
            if not is_file_field:
                raise BadRequestException(f"{snapshot.get('label') or field_key} does not accept attachments.")
            asset_ids.append(int(item.asset_id))

        if field_type in {"select", "dictionary", "multiselect"} and not is_blank:
            allowed_values = _normalize_option_values(snapshot.get("options"))
            if allowed_values:
                submitted_values = _normalize_submitted_option_values(item.value, item.display_value)
                unsupported_values = [value for value in submitted_values if value not in allowed_values]
                if unsupported_values:
                    raise BadRequestException(f"Invalid option for {snapshot.get('label') or field_key}.")

        if field_type == "number" and not is_blank:
            try:
                float(str(item.value).strip())
            except (TypeError, ValueError):
                raise BadRequestException(f"{snapshot.get('label') or field_key} must be a number.")

    missing_required_fields = [
        field
        for field in hydrated_fields
        if bool(field.get("required")) and str(field.get("key")) not in submitted_keys
    ]
    if missing_required_fields:
        first_missing = missing_required_fields[0]
        raise BadRequestException(f"{first_missing.get('label') or first_missing.get('key')} is required.")

    if asset_ids:
        assets = await ensure_assets_belong_to_owner(
            db,
            owner_type="user",
            owner_id=int(current_user["id"]),
            asset_ids=asset_ids,
        )
        invalid_assets = [
            asset.id
            for asset in assets
            if asset.module != "candidate_application" or asset.is_deleted
        ]
        if invalid_assets:
            raise BadRequestException("Invalid application attachment.")

    return field_snapshot_map, list(payload.items)


def _merge_fields_into_profile(
    talent: TalentProfile,
    field_rows: Sequence[CandidateApplicationFieldValue],
    *,
    allowed_catalog_keys: set[str] | None = None,
) -> list[str]:
    merged_fields: list[str] = []
    for row in field_rows:
        catalog_key = row.catalog_key or row.field_key
        if allowed_catalog_keys is not None and catalog_key not in allowed_catalog_keys:
            continue

        display_value = row.display_value or row.raw_value
        if catalog_key in TALENT_FIELD_MAPPING and display_value is not None:
            setattr(talent, TALENT_FIELD_MAPPING[catalog_key], display_value)
            merged_fields.append(catalog_key)
        elif catalog_key in TALENT_ASSET_FIELD_MAPPING and row.asset_id is not None:
            setattr(talent, TALENT_ASSET_FIELD_MAPPING[catalog_key], row.asset_id)
            merged_fields.append(catalog_key)
    return merged_fields


async def _get_job_for_application(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
            Job.status == JobStatus.OPEN.value,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    return job


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
        job_company_name_map = {
            int(job_id): company_name
            for job_id, company_name in job_result.all()
        }
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

    applications = [
        CandidateApplicationSummaryRead(
            id=application.id,
            job_id=application.job_id,
            job_snapshot_title=application.job_snapshot_title,
            job_company_name=job_company_name_map.get(application.job_id),
            status=application.status,
            status_cn_name=get_candidate_application_status_cn_name(application.status),
            current_stage=progress_map.get(application.id).current_stage if progress_map.get(application.id) else None,
            current_stage_cn_name=(
                get_recruitment_stage_cn_name(progress_map.get(application.id).current_stage)
                if progress_map.get(application.id)
                else None
            ),
            submitted_at=application.submitted_at,
            source_of_current_snapshot=application.id == talent.source_application_id,
        ).model_dump()
        for application in application_models
    ]

    pending_merge = await _build_pending_merge_payload(
        talent=talent,
        db=db,
        applications=application_models,
        current_resume_asset_name=asset_name,
    )
    logs = await _list_talent_operation_logs(talent=talent, db=db)
    timesheet_records = await _list_talent_timesheet_records(talent=talent, db=db)
    payment_records = await _list_talent_payment_records(talent=talent, db=db)

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
        payment_records=payment_records,
        pending_merge=pending_merge,
        logs=logs,
        **extra_fields,
    ).model_dump()


async def _list_talent_timesheet_records(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[dict[str, Any]]:
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
        ).model_dump()
        for record in result.scalars().all()
    ]


async def _list_talent_payment_records(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(PaymentRecord)
        .where(
            PaymentRecord.is_deleted.is_(False),
            or_(
                PaymentRecord.talent_profile_id == talent.id,
                PaymentRecord.user_id == talent.user_id,
            ),
        )
        .order_by(PaymentRecord.paid_at.desc(), PaymentRecord.id.desc())
        .limit(50)
    )
    return [
        TalentPaymentRecordRead(
            id=record.id,
            paid_at=record.paid_at,
            payment_type=record.payment_type,
            amount=record.amount,
            currency=record.currency,
            project_name=record.project_snapshot_name,
            contract_ref_no=record.contract_snapshot_ref_no,
            external_transaction_no=record.external_transaction_no,
            remark=record.remark,
        ).model_dump()
        for record in result.scalars().all()
    ]


async def _list_talent_operation_logs(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[dict[str, Any]]:
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
            int(application_id): job_snapshot_title
            for application_id, job_snapshot_title in application_result.all()
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

    items: list[dict[str, Any]] = []
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
            ).model_dump()
        )
    return items


async def _build_pending_merge_payload(
    *,
    talent: TalentProfile,
    db: AsyncSession,
    applications: Sequence[CandidateApplication],
    current_resume_asset_name: str | None,
) -> dict[str, Any] | None:
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

    field_diffs: list[dict[str, Any]] = []
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
            ).model_dump()
        )

    if not field_diffs:
        return None

    return TalentPendingMergeRead(
        application_id=latest_application.id,
        submitted_at=latest_application.submitted_at,
        fields=field_diffs,
    ).model_dump()


async def create_application_and_sync_talent(
    *,
    job_id: int,
    payload: CandidateApplicationSubmitRequest,
    current_user: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    job = await _get_job_for_application(job_id, db)
    existing_application_result = await db.execute(
        select(CandidateApplication.id).where(
            CandidateApplication.user_id == current_user["id"],
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    if existing_application_result.scalar_one_or_none() is not None:
        raise BadRequestException("You have already applied to this role.")
    field_snapshot_map, validated_items = await _validate_application_items(
        job=job,
        payload=payload,
        current_user=current_user,
        db=db,
    )

    application = CandidateApplication(
        user_id=current_user["id"],
        job_id=job.id,
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        status="submitted",
        submitted_at=datetime.now(UTC),
        data={"submitted_items_count": len(validated_items)},
    )
    db.add(application)
    await db.flush()

    job.applicant_count = int(job.applicant_count or 0) + 1
    if isinstance(job.data, dict):
        application_summary = job.data.get(JOB_DATA_APPLICATION_SUMMARY_KEY)
        if isinstance(application_summary, dict):
            next_summary = dict(application_summary)
            next_summary["applicants"] = int(job.applicant_count)
            next_data = dict(job.data)
            next_data[JOB_DATA_APPLICATION_SUMMARY_KEY] = next_summary
            job.data = next_data

    next_order = 0
    for item in validated_items:
        snapshot = field_snapshot_map.get(item.field_key, {})
        display_value = item.display_value or _normalize_display_value(item.value)
        row = CandidateApplicationFieldValue(
            application_id=application.id,
            field_key=item.field_key,
            field_label=str(snapshot.get("label") or item.field_key),
            field_type=str(snapshot.get("type") or "text"),
            catalog_key=item.field_key,
            raw_value=_serialize_raw_value(item.value),
            display_value=display_value,
            asset_id=item.asset_id,
            sort_order=next_order,
        )
        db.add(row)
        next_order += 1

    await db.flush()

    submitted_log = await create_operation_log(
        db=db,
        user_id=current_user["id"],
        job_id=job.id,
        application_id=application.id,
        log_type=OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value,
        data={
            "application_id": application.id,
            "job_id": job.id,
            "job_title": job.title,
            "submitted_items_count": len(validated_items),
        },
    )

    talent_result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == current_user["id"],
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = talent_result.scalar_one_or_none()

    auto_merged = False
    talent_created = False
    if talent is None:
        talent = TalentProfile(
            user_id=current_user["id"],
            full_name=current_user.get("name"),
            email=current_user.get("email"),
            latest_applied_job_id=job.id,
            latest_applied_job_title=job.title,
            latest_applied_at=application.submitted_at,
            source_application_id=application.id,
            merge_strategy=TalentMergeStrategy.INITIAL_AUTO.value,
            last_merged_at=application.submitted_at,
            data={},
        )
        db.add(talent)
        await db.flush()

        field_rows = await _list_application_field_rows(application.id, db)
        merged_fields = _merge_fields_into_profile(talent, field_rows)
        if CandidateFieldKey.EMAIL.value not in merged_fields and current_user.get("email"):
            talent.email = current_user["email"]
        if CandidateFieldKey.FULL_NAME.value not in merged_fields and current_user.get("name"):
            talent.full_name = current_user["name"]

        db.add(
            TalentProfileMergeLog(
                talent_profile_id=talent.id,
                application_id=application.id,
                operator_admin_user_id=None,
                merge_strategy=TalentMergeStrategy.INITIAL_AUTO.value,
                merged_fields=merged_fields,
            )
        )
        await create_operation_log(
            db=db,
            user_id=current_user["id"],
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent.id,
            log_type=OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value,
            data={
                "talent_profile_id": talent.id,
                "application_id": application.id,
                "job_id": job.id,
                "job_title": job.title,
                "merged_fields": merged_fields,
            },
        )
        auto_merged = True
        talent_created = True
    else:
        talent.latest_applied_job_id = job.id
        talent.latest_applied_job_title = job.title
        talent.latest_applied_at = application.submitted_at
        await create_operation_log(
            db=db,
            user_id=current_user["id"],
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent.id,
            log_type=OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value,
            data={
                "talent_profile_id": talent.id,
                "application_id": application.id,
                "latest_applied_job_id": job.id,
                "job_title": job.title,
            },
        )

    submitted_log.talent_profile_id = talent.id

    field_rows = await _list_application_field_rows(application.id, db)
    await create_job_progress_for_application(
        job=job,
        application=application,
        talent_profile_id=talent.id,
        field_rows=field_rows,
        db=db,
    )

    await db.commit()

    return CandidateApplicationSubmitResponse(
        application_id=application.id,
        talent_profile_id=talent.id,
        talent_profile_created=talent_created,
        auto_merged=auto_merged,
    ).model_dump()


async def merge_application_into_talent(
    *,
    talent_id: int,
    application_id: int,
    current_admin: dict[str, Any],
    db: AsyncSession,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    application = await _get_application_model(application_id, db)
    if application.user_id != talent.user_id:
        raise BadRequestException("Application does not belong to this talent profile.")

    field_rows = await _list_application_field_rows(application.id, db)
    merged_fields = _merge_fields_into_profile(
        talent,
        field_rows,
        allowed_catalog_keys=set(fields) if fields else None,
    )
    if not merged_fields:
        raise BadRequestException("No eligible fields were found to merge.")

    talent.source_application_id = application.id
    talent.merge_strategy = TalentMergeStrategy.MANUAL.value
    talent.last_merged_at = datetime.now(UTC)
    talent.latest_applied_job_id = application.job_id
    talent.latest_applied_job_title = application.job_snapshot_title
    talent.latest_applied_at = application.submitted_at

    db.add(
        TalentProfileMergeLog(
            talent_profile_id=talent.id,
            application_id=application.id,
            operator_admin_user_id=current_admin["id"],
            merge_strategy=TalentMergeStrategy.MANUAL.value,
            merged_fields=merged_fields,
        )
    )
    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=application.job_id,
        application_id=application.id,
        talent_profile_id=talent.id,
        log_type=OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value,
        data={
            "talent_profile_id": talent.id,
            "application_id": application.id,
            "job_id": application.job_id,
            "job_title": application.job_snapshot_title,
            "operator_admin_user_id": current_admin["id"],
            "merged_fields": merged_fields,
        },
    )
    await db.commit()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)


async def get_talent_profile(talent_id: int, db: AsyncSession) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    return await _serialize_talent_profile(talent, db)


async def get_talent_profile_by_user_id(user_id: int, db: AsyncSession) -> dict[str, Any]:
    talent = await _get_talent_profile_model_by_user_id(user_id, db)
    return await _serialize_talent_profile(talent, db)


async def update_talent_pool_note(
    *,
    talent_id: int,
    note: str | None,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    normalized_note = (note or "").strip() or None
    if progress is not None:
        progress.data = {
            **(progress.data or {}),
            JobProgressDataKey.NOTE.value: normalized_note,
        }
    talent.note = normalized_note
    await db.commit()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)


async def update_talent_pool_status(
    *,
    talent_id: int,
    status: str,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    try:
        normalized_status = validate_manual_talent_status(status, progress)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    talent.data = {
        **(talent.data or {}),
        TALENT_STATUS_OVERRIDE_KEY: normalized_status,
    }
    if normalized_status == TALENT_STATUS_REPLACED and progress is not None:
        progress.current_stage = RecruitmentStage.REPLACED.value
    await db.commit()
    await db.refresh(talent)
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
    conditions = [TalentProfile.is_deleted.is_(False)]
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
            )
        )

    if company_id is not None or project_id is not None:
        application_conditions = [
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

    total_result = await db.execute(
        select(func.count())
        .select_from(TalentProfile)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)
    paged_result = await db.execute(
        base_query.offset((page - 1) * page_size).limit(page_size)
    )
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
            note=(
                extra_fields := build_talent_pool_extra_fields(talent, source_bundle)
            ).pop("note", talent.note),
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
