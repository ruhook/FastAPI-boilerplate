import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.advanced_filter import (
    AdvancedFilterFieldDefinition,
    build_advanced_filter_query_sql_condition,
    has_advanced_filter_rules,
    parse_advanced_filter_query,
    validate_advanced_filter_query,
)
from ...core.config import settings
from ...core.db.database import local_session
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..admin.dictionary.service import get_dictionary_option_label_map_by_key
from ..admin.internal_notification.service import create_admin_internal_notification
from ..admin.mail_task.const import MAIL_TASK_DATA_RENDER_CONTEXT_KEY, MailTaskStatus
from ..admin.mail_task.model import MailTask
from ..admin.mail_task.schema import MailRecipient, MailTaskCreate
from ..admin.mail_task.service import create_mail_task, dispatch_mail_task_created_event
from ..admin.mail_template.service import get_mail_template_model
from ..assets.model import Asset
from ..assets.schema import AssetUploadPayload
from ..assets.service import serialize_asset, upload_asset
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_internal_notification.service import create_candidate_internal_notification
from ..contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_EXPIRED,
    CONTRACT_STATUS_TERMINATED,
)
from ..contract_record.model import ContractRecord
from ..contract_record.service import (
    get_current_contract_record_by_progress_id,
    get_default_contract_end_date,
    list_current_contract_records_by_progress_ids,
    upsert_contract_record_for_progress,
)
from ..job.const import (
    JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY,
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_LANGUAGES_KEY,
    JOB_DATA_SHOW_COMPENSATION_KEY,
)
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..referral_bonus_model.service import ensure_user_referral_profile_from_job
from ..user.model import User
from .const import (
    JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP,
    JobProgressDataKey,
    RecruitmentScreeningMode,
    RecruitmentStage,
    get_allowed_recruitment_stage_transitions,
    get_recruitment_stage_cn_name,
)
from .model import JobProgress
from .schema import (
    CandidateContractListItemRead,
    CandidateContractListPage,
    CandidateJobApplicationDetailRead,
    CandidateJobApplicationListItemRead,
    CandidateJobApplicationListPage,
    ContractRecordDataRead,
    JobProgressAssessmentInviteMarkResponse,
    JobProgressAssessmentUploadResponse,
    JobProgressCandidateSignedContractUploadResponse,
    JobProgressCompanySealedContractUploadResponse,
    JobProgressContractAssetRead,
    JobProgressContractDraftUploadResponse,
    JobProgressContractRecordUpdateItemRead,
    JobProgressContractRecordUpdateResponse,
    JobProgressListItemRead,
    JobProgressListPage,
    JobProgressNotifySignContractResponse,
    JobProgressOnboardingUpdateResponse,
    JobProgressRead,
)

logger = logging.getLogger(__name__)

ADVANCED_FILTER_BACKEND_STAGE_MAP: dict[str, str] = {
    "screening": RecruitmentStage.PENDING_SCREENING.value,
    "assessment": RecruitmentStage.ASSESSMENT_REVIEW.value,
    "passed": RecruitmentStage.SCREENING_PASSED.value,
    "contract": RecruitmentStage.CONTRACT_POOL.value,
    "employed": RecruitmentStage.ACTIVE.value,
    "replaced": RecruitmentStage.REPLACED.value,
    "eliminated": RecruitmentStage.REJECTED.value,
}

CONTRACT_PROCESS_DATA_KEYS = {
    JobProgressDataKey.ACCEPTED_RATE.value,
    JobProgressDataKey.SIGNING_STATUS.value,
    JobProgressDataKey.CONTRACT_NUMBER.value,
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT.value,
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID.value,
    JobProgressDataKey.SUBMITTED_CONTRACT_AT.value,
    JobProgressDataKey.CONTRACT_REVIEW.value,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT.value,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID.value,
}

CONTRACT_PROCESS_ASSET_KEYS = {
    JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT,
    JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT,
    JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT,
}

CONTRACT_RECORD_FIELD_STAGE_MAP: dict[str, set[str]] = {
    "agreement_ref_no": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "rate": {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "signing_status": {
        RecruitmentStage.SCREENING_PASSED.value,
    },
    "contract_review": {
        RecruitmentStage.CONTRACT_POOL.value,
    },
    "end_date": {
        RecruitmentStage.CONTRACT_POOL.value,
    },
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_language_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_normalize_text(item) for item in value) if item]
    normalized = _normalize_text(value)
    return [normalized] if normalized else []


def _has_asset_id(value: Any) -> bool:
    return _normalize_text(value).lower() not in {"", "0", "none", "null"}


def _has_assessment_attachment(progress: JobProgress) -> bool:
    progress_data = progress.data or {}
    if _has_asset_id(progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value)):
        return True

    raw_submissions = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    if not isinstance(raw_submissions, list):
        return False
    return any(isinstance(item, dict) and _has_asset_id(item.get("asset_id")) for item in raw_submissions)


def _normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def _map_backend_stage_to_progress_stage(stage: str) -> str:
    if stage == RecruitmentStage.PENDING_SCREENING.value:
        return "screening"
    if stage == RecruitmentStage.ASSESSMENT_REVIEW.value:
        return "assessment"
    if stage == RecruitmentStage.SCREENING_PASSED.value:
        return "passed"
    if stage == RecruitmentStage.CONTRACT_POOL.value:
        return "contract"
    if stage == RecruitmentStage.ACTIVE.value:
        return "employed"
    if stage == RecruitmentStage.REPLACED.value:
        return "replaced"
    return "eliminated"


def _build_rejected_from_stage_progress_stage_sql_expression():
    rejected_from_stage_expr = _build_progress_json_text_expression(JobProgressDataKey.REJECTED_FROM_STAGE.value)
    is_rejected = JobProgress.current_stage == RecruitmentStage.REJECTED.value
    return case(
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.PENDING_SCREENING.value, "screening"])),
            "screening",
        ),
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.ASSESSMENT_REVIEW.value, "assessment"])),
            "assessment",
        ),
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.SCREENING_PASSED.value, "passed"])),
            "passed",
        ),
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.CONTRACT_POOL.value, "contract"])),
            "contract",
        ),
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.ACTIVE.value, "employed"])),
            "employed",
        ),
        (
            and_(is_rejected, rejected_from_stage_expr.in_([RecruitmentStage.REPLACED.value, "replaced"])),
            "replaced",
        ),
        else_="",
    )


def _serialize_rejected_from_stage_for_filter(item: JobProgressListItemRead) -> str:
    if item.current_stage != RecruitmentStage.REJECTED.value:
        return ""
    raw_stage = _normalize_text(item.process_data.get(JobProgressDataKey.REJECTED_FROM_STAGE.value))
    if raw_stage in {"screening", "assessment", "passed", "contract", "employed", "replaced"}:
        return raw_stage
    if not raw_stage:
        return ""
    mapped_stage = _map_backend_stage_to_progress_stage(raw_stage)
    return "" if mapped_stage == "eliminated" else mapped_stage


def _normalize_progress_filter_field_kind(field_type: str | None) -> str:
    normalized = _normalize_text(field_type).lower()
    if normalized in {"boolean", "select"}:
        return "select"
    if normalized == "multiselect":
        return "multiselect"
    if normalized == "file":
        return "file"
    if normalized == "number":
        return "number"
    if normalized == "email":
        return "email"
    return "text"


def _build_progress_stage_sql_expression():
    return case(
        (JobProgress.current_stage == RecruitmentStage.PENDING_SCREENING.value, "screening"),
        (JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value, "assessment"),
        (JobProgress.current_stage == RecruitmentStage.SCREENING_PASSED.value, "passed"),
        (JobProgress.current_stage == RecruitmentStage.CONTRACT_POOL.value, "contract"),
        (JobProgress.current_stage == RecruitmentStage.ACTIVE.value, "employed"),
        (JobProgress.current_stage == RecruitmentStage.REPLACED.value, "replaced"),
        else_="eliminated",
    )


def _build_progress_application_field_sql_expression(
    *,
    field_key: str,
    asset_only: bool = False,
):
    return (
        select(
            CandidateApplicationFieldValue.asset_id
            if asset_only
            else func.coalesce(
                CandidateApplicationFieldValue.display_value,
                CandidateApplicationFieldValue.raw_value,
            )
        )
        .where(
            CandidateApplicationFieldValue.application_id == JobProgress.application_id,
            or_(
                CandidateApplicationFieldValue.catalog_key == field_key,
                and_(
                    CandidateApplicationFieldValue.catalog_key.is_(None),
                    CandidateApplicationFieldValue.field_key == field_key,
                ),
            ),
        )
        .order_by(
            CandidateApplicationFieldValue.sort_order.asc(),
            CandidateApplicationFieldValue.id.asc(),
        )
        .limit(1)
        .scalar_subquery()
    )


def _build_progress_json_text_expression(key: str):
    return func.json_unquote(func.json_extract(JobProgress.data, f"$.{key}"))


def _build_job_languages_sql_expression(job: Job | None):
    snapshot_expr = _build_progress_json_text_expression(JobProgressDataKey.JOB_LANGUAGES.value)
    snapshot_text = func.replace(func.replace(func.replace(snapshot_expr, '"', ""), "[", ""), "]", "")
    fallback_languages = _normalize_language_values((job.data or {}).get(JOB_DATA_LANGUAGES_KEY)) if job else []
    fallback_text = " / ".join(fallback_languages)
    return func.coalesce(func.nullif(snapshot_text, ""), fallback_text)


def _build_progress_assessment_attachment_filter_expression():
    legacy_asset_id = func.lower(
        func.trim(
            func.coalesce(
                _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value),
                "",
            )
        )
    )
    legacy_name = func.trim(
        func.coalesce(
            _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_ATTACHMENT.value),
            "",
        )
    )
    submission_count = func.coalesce(
        func.json_length(func.json_extract(JobProgress.data, f"$.{JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value}")),
        0,
    )
    return case(
        (
            or_(
                submission_count > 0,
                legacy_asset_id.notin_(["", "0", "none", "null"]),
                legacy_name != "",
            ),
            "submitted",
        ),
        else_="",
    )


def _build_progress_contract_sql_expression(
    *,
    column_name: str | None = None,
    data_key: str | None = None,
):
    if not column_name and not data_key:
        raise ValueError("column_name or data_key is required.")
    if data_key:
        expression = func.json_unquote(func.json_extract(ContractRecord.data, f"$.{data_key}"))
    else:
        expression = getattr(ContractRecord, column_name or "")
    return (
        select(expression)
        .where(
            ContractRecord.job_progress_id == JobProgress.id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )


def _build_progress_advanced_filter_field_map(job: Job | None) -> dict[str, AdvancedFilterFieldDefinition]:  # noqa: C901
    field_map: dict[str, AdvancedFilterFieldDefinition] = {}

    form_fields = []
    if job is not None:
        form_fields = list((job.data or {}).get(JOB_DATA_FORM_FIELDS_KEY) or [])

    for field in form_fields:
        if not isinstance(field, dict):
            continue
        key = _normalize_text(field.get("key"))
        if not key:
            continue
        field_map[key] = AdvancedFilterFieldDefinition(
            name=key,
            filter_kind=_normalize_progress_filter_field_kind(field.get("type")),
            sql_expression=_build_progress_application_field_sql_expression(
                field_key=key,
                asset_only=_normalize_progress_filter_field_kind(field.get("type")) == "file",
            ),
        )

    process_field_kinds = {
        "current_stage": "select",
        "applied_at": "date",
        "assessment_attachment": "file",
        "assessment_sent_at": "date",
        "assessment_submitted_at": "date",
        "assessment_result": "select",
        "assessment_review_comment": "text",
        "assessment_reviewer": "select",
        "qa_status": "select",
        "qa_feedback": "text",
        "signing_status": "select",
        "contract_number": "text",
        "contract_draft_attachment": "file",
        "accepted_rate": "number",
        "effective_date": "date",
        "end_date": "date",
        "id_attachment": "file",
        "submitted_contract_attachment": "file",
        "submitted_contract_at": "date",
        "contract_review": "select",
        "contract_return_attachment": "file",
        "onboarding_status": "select",
        "onboarding_date": "date",
        "gift_package_sent_at": "date",
        "job_languages": "multiselect",
        "rejected_from_stage": "select",
        "replacement_reason": "text",
        "note": "text",
    }
    for name, filter_kind in process_field_kinds.items():
        sql_expression = None
        if name == "current_stage":
            sql_expression = _build_progress_stage_sql_expression()
        elif name == "applied_at":
            sql_expression = CandidateApplication.submitted_at
        elif name == "assessment_attachment":
            sql_expression = _build_progress_assessment_attachment_filter_expression()
        elif name == "assessment_sent_at":
            assessment_sent_at_expr = _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_SENT_AT.value)
            assessment_invited_at_expr = _build_progress_json_text_expression(
                JobProgressDataKey.ASSESSMENT_INVITED_AT.value
            )
            sql_expression = func.substr(
                func.coalesce(func.nullif(assessment_sent_at_expr, ""), assessment_invited_at_expr),
                1,
                10,
            )
        elif name == "assessment_submitted_at":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value)
        elif name == "assessment_result":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_RESULT.value)
        elif name == "assessment_review_comment":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value)
        elif name == "assessment_reviewer":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ASSESSMENT_REVIEWER.value)
        elif name == "qa_status":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.QA_STATUS.value)
        elif name == "qa_feedback":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.QA_FEEDBACK.value)
        elif name == "signing_status":
            sql_expression = _build_progress_contract_sql_expression(data_key="signing_status")
        elif name == "contract_number":
            sql_expression = _build_progress_contract_sql_expression(column_name="agreement_ref_no")
        elif name == "contract_draft_attachment":
            sql_expression = _build_progress_contract_sql_expression(column_name="draft_contract_asset_id")
        elif name == "accepted_rate":
            sql_expression = _build_progress_contract_sql_expression(column_name="rate")
        elif name == "effective_date":
            sql_expression = _build_progress_contract_sql_expression(column_name="effective_date")
        elif name == "end_date":
            sql_expression = _build_progress_contract_sql_expression(column_name="end_date")
        elif name == "id_attachment":
            sql_expression = (
                select(func.json_unquote(func.json_extract(User.data, "$.payment_info.id_attachment_asset_id")))
                .where(
                    User.id == JobProgress.user_id,
                    User.is_deleted.is_(False),
                )
                .limit(1)
                .scalar_subquery()
            )
        elif name == "submitted_contract_attachment":
            sql_expression = _build_progress_contract_sql_expression(column_name="candidate_signed_contract_asset_id")
        elif name == "submitted_contract_at":
            sql_expression = _build_progress_contract_sql_expression(data_key="candidate_signed_contract_submitted_at")
        elif name == "contract_review":
            sql_expression = _build_progress_contract_sql_expression(data_key="contract_review")
        elif name == "contract_return_attachment":
            sql_expression = _build_progress_contract_sql_expression(column_name="company_sealed_contract_asset_id")
        elif name == "onboarding_status":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ONBOARDING_STATUS.value)
        elif name == "onboarding_date":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.ONBOARDING_DATE.value)
        elif name == "gift_package_sent_at":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
        elif name == "job_languages":
            sql_expression = _build_job_languages_sql_expression(job)
        elif name == "rejected_from_stage":
            sql_expression = _build_rejected_from_stage_progress_stage_sql_expression()
        elif name == "replacement_reason":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.REPLACEMENT_REASON.value)
        elif name == "note":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.NOTE.value)
        field_map[name] = AdvancedFilterFieldDefinition(
            name=name,
            filter_kind=filter_kind,  # type: ignore[arg-type]
            sql_expression=sql_expression,
        )

    return field_map


def _serialize_filter_record_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return _ensure_utc_datetime(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _normalize_text(value)


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _get_company_name_map_by_job_ids(
    *,
    job_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_job_ids = sorted({int(job_id) for job_id in job_ids if job_id})
    if not normalized_job_ids:
        return {}
    result = await db.execute(
        select(Job.id, AdminCompany.name)
        .outerjoin(AdminCompany, AdminCompany.id == Job.company_id)
        .where(
            Job.id.in_(normalized_job_ids),
            Job.is_deleted.is_(False),
        )
    )
    return {int(job_id): company_name for job_id, company_name in result.all() if company_name}


async def _get_company_name_map_by_company_ids(
    *,
    company_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_company_ids = sorted({int(company_id) for company_id in company_ids if company_id})
    if not normalized_company_ids:
        return {}
    result = await db.execute(
        select(AdminCompany.id, AdminCompany.name).where(
            AdminCompany.id.in_(normalized_company_ids),
            AdminCompany.is_deleted.is_(False),
        )
    )
    return {int(company_id): company_name for company_id, company_name in result.all() if company_name}


async def _get_project_name_map_by_job_ids(
    *,
    job_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_job_ids = sorted({int(job_id) for job_id in job_ids if job_id})
    if not normalized_job_ids:
        return {}
    result = await db.execute(
        select(Job.id, AdminCompanyProject.name)
        .outerjoin(AdminCompanyProject, AdminCompanyProject.id == Job.project_id)
        .where(
            Job.id.in_(normalized_job_ids),
            Job.is_deleted.is_(False),
        )
    )
    return {int(job_id): project_name for job_id, project_name in result.all() if project_name}


async def _get_project_name_map_by_project_ids(
    *,
    project_ids: list[int],
    db: AsyncSession,
) -> dict[int, str]:
    normalized_project_ids = sorted({int(project_id) for project_id in project_ids if project_id})
    if not normalized_project_ids:
        return {}
    result = await db.execute(
        select(AdminCompanyProject.id, AdminCompanyProject.name).where(
            AdminCompanyProject.id.in_(normalized_project_ids),
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    return {int(project_id): project_name for project_id, project_name in result.all() if project_name}


def _validate_contract_record_update_stage(*, stage: str, changed_fields: list[str]) -> None:
    unsupported_fields = sorted(
        {field for field in changed_fields if stage not in CONTRACT_RECORD_FIELD_STAGE_MAP.get(field, set())}
    )
    if unsupported_fields:
        stage_name = get_recruitment_stage_cn_name(stage)
        raise BadRequestException(f"Contract fields {', '.join(unsupported_fields)} cannot be updated in {stage_name}.")


def _build_field_value_map(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, dict[str, Any]]:
    value_map: dict[str, dict[str, Any]] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        value_map[key] = {
            "raw_value": row.raw_value,
            "display_value": row.display_value,
            "asset_id": row.asset_id,
        }
    return value_map


def _evaluate_automation_rule(
    rule: dict[str, Any],
    field_values: dict[str, dict[str, Any]],
) -> bool:
    field_key = _normalize_text(rule.get("fieldKey"))
    operator = _normalize_text(rule.get("operator")).lower()
    configured_value = rule.get("value")
    field_entry = field_values.get(field_key, {})
    display_value = field_entry.get("display_value")
    raw_value = field_entry.get("raw_value")
    asset_id = field_entry.get("asset_id")
    actual_value = raw_value if raw_value is not None else display_value
    raw_text = _normalize_text(raw_value).lower()
    display_text = _normalize_text(display_value).lower()

    if operator == "uploaded":
        return asset_id is not None or _normalize_text(actual_value) != ""
    if operator == "not_uploaded":
        return asset_id is None and _normalize_text(actual_value) == ""
    if operator == "true":
        return _normalize_text(actual_value).lower() in {"true", "1", "yes"}
    if operator == "false":
        return _normalize_text(actual_value).lower() in {"false", "0", "no"}

    if operator in {"gt", "lt", "eq", "between"}:
        left = _normalize_number(actual_value)
        if left is None:
            return False
        if operator == "gt":
            right = _normalize_number(configured_value)
            return right is not None and left > right
        if operator == "lt":
            right = _normalize_number(configured_value)
            return right is not None and left < right
        if operator == "eq":
            right = _normalize_number(configured_value)
            if right is not None:
                return left == right
            return _normalize_text(actual_value).lower() == _normalize_text(configured_value).lower()
        if operator == "between":
            lower = _normalize_number(configured_value)
            upper = _normalize_number(rule.get("secondValue"))
            return lower is not None and upper is not None and lower <= left <= upper

    actual_text = _normalize_text(actual_value).lower()
    normalized_actual_parts = {
        value.strip().lower()
        for source in {actual_text, raw_text, display_text}
        for value in source.replace("/", ",").split(",")
        if value.strip()
    }
    if operator == "contains":
        target = _normalize_text(configured_value).lower()
        return any(target in source for source in {actual_text, raw_text, display_text})
    if operator == "not_contains":
        target = _normalize_text(configured_value).lower()
        return all(target not in source for source in {actual_text, raw_text, display_text})
    if operator == "includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        return any(_normalize_text(item).lower() in normalized_actual_parts for item in target_values)
    if operator == "not_includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        return all(_normalize_text(item).lower() not in normalized_actual_parts for item in target_values)
    if operator == "eq":
        target = _normalize_text(configured_value).lower()
        return target in {actual_text, raw_text, display_text}

    return False


def _evaluate_automation_rules(
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[bool, bool]:
    data = job.data or {}
    rule_group = data.get(JOB_DATA_AUTOMATION_RULES_KEY) or {}
    rules = list(rule_group.get("rules") or [])
    if not rules:
        return False, False

    combinator = _normalize_text(rule_group.get("combinator") or "and").lower()
    field_values = _build_field_value_map(field_rows)
    normalized_rules = [rule for rule in rules if isinstance(rule, dict)]

    def is_any_group(rule: dict[str, Any]) -> bool:
        return _normalize_text(rule.get("group")).lower() in {"any", "or"}

    any_group_rules = [rule for rule in normalized_rules if is_any_group(rule)]
    if any_group_rules:
        required_results = [
            _evaluate_automation_rule(rule, field_values)
            for rule in normalized_rules
            if not is_any_group(rule)
        ]
        any_results = [_evaluate_automation_rule(rule, field_values) for rule in any_group_rules]
        matched = all(required_results) and (not any_results or any(any_results))
        return True, matched

    results = [_evaluate_automation_rule(rule, field_values) for rule in normalized_rules]
    if not results:
        return False, False
    matched = all(results) if combinator != "or" else any(results)
    return True, matched


def _resolve_initial_stage(
    *,
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[RecruitmentStage, RecruitmentScreeningMode, str, bool]:
    auto_screening_enabled, matched = _evaluate_automation_rules(job, field_rows)
    if not auto_screening_enabled:
        return (
            RecruitmentStage.PENDING_SCREENING,
            RecruitmentScreeningMode.MANUAL,
            "岗位未配置自动筛选规则，申请停留在待筛选名单。",
            False,
        )

    if matched:
        return (
            RecruitmentStage.PENDING_SCREENING,
            RecruitmentScreeningMode.AUTO,
            "自动筛选通过，申请停留在待筛选名单，等待测试题提交或人工处理。",
            True,
        )
    return (
        RecruitmentStage.PENDING_SCREENING,
        RecruitmentScreeningMode.AUTO,
        "自动筛选未通过，申请保留在待筛选名单等待人工处理。",
        False,
    )


def _build_candidate_assessment_url(application_id: int | None) -> str:
    if application_id is None:
        return ""
    base_url = settings.CANDIDATE_WEB_BASE_URL.strip().rstrip("/")
    path = f"/my-assessments/{application_id}"
    return f"{base_url}{path}" if base_url else path


def _get_job_mail_context(job: Job, company_name: str | None, *, application_id: int | None = None) -> dict[str, Any]:
    job_data = job.data or {}
    resolved_company_name = (company_name or "").strip()
    assessment_external_url = str(
        job_data.get(JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY)
        or job_data.get("assessment_external_url")
        or job_data.get("assessment_link")
        or job_data.get("assessmentLink")
        or ""
    )
    assessment_link = assessment_external_url or _build_candidate_assessment_url(application_id)
    return {
        "job": {
            "title": job.title,
            "job_title": job.title,
            "assessment_link": assessment_link,
            "due_date": str(job_data.get("due_date") or job_data.get("dueDate") or ""),
        },
        "company": {
            "name": resolved_company_name,
            "company_name": resolved_company_name,
        },
    }


def _build_candidate_contract_upload_url(application_id: int | None) -> str:
    if application_id is None:
        return ""
    base_url = settings.CANDIDATE_WEB_BASE_URL.strip().rstrip("/")
    path = f"/my-contracts/{application_id}/upload"
    return f"{base_url}{path}" if base_url else path


def _contains_contract_upload_url_variable(content: str) -> bool:
    compact = "".join((content or "").lower().split())
    return "{{contract_upload_url}}" in compact or "%7b%7bcontract_upload_url%7d%7d" in compact


def _get_stage_mail_config(job: Job, target_stage: RecruitmentStage) -> dict[str, int] | None:
    if target_stage == RecruitmentStage.ASSESSMENT_REVIEW:
        if not (
            job.assessment_enabled
            and job.assessment_mail_account_id is not None
            and job.assessment_mail_template_id is not None
            and job.assessment_mail_signature_id is not None
        ):
            return None
        return {
            "account_id": int(job.assessment_mail_account_id),
            "template_id": int(job.assessment_mail_template_id),
            "signature_id": int(job.assessment_mail_signature_id),
        }

    if target_stage == RecruitmentStage.REJECTED:
        raw_config = (job.data or {}).get("rejection_mail_config") or {}
        if not isinstance(raw_config, dict) or not raw_config.get("enabled"):
            return None

        account_id = raw_config.get("mail_account_id")
        template_id = raw_config.get("mail_template_id")
        signature_id = raw_config.get("mail_signature_id")
        if account_id is None or template_id is None or signature_id is None:
            return None

        return {
            "account_id": int(account_id),
            "template_id": int(template_id),
            "signature_id": int(signature_id),
        }

    return None


async def _record_stage_mail_operation(
    *,
    job: Job,
    application: CandidateApplication,
    target_stage: RecruitmentStage,
    db: AsyncSession,
    log_type: OperationLogType,
    reason: str | None = None,
    mail_task_id: int | None = None,
) -> None:
    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=job.id,
        application_id=application.id,
        log_type=log_type.value,
        data={
            "job_id": job.id,
            "job_title": job.title,
            "target_stage": target_stage.value,
            "target_stage_cn_name": get_recruitment_stage_cn_name(target_stage.value),
            "reason": reason or "",
            "mail_task_id": mail_task_id,
        },
    )


def _has_assessment_invitation(progress: JobProgress) -> bool:
    data = progress.data or {}
    return bool(
        _normalize_text(data.get(JobProgressDataKey.ASSESSMENT_INVITED_AT.value))
        or _normalize_text(data.get(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value))
    )


CANDIDATE_VISIBLE_STAGE_LABELS = {
    "review": "Review",
    "assessment_file": "Assessment File",
    RecruitmentStage.ASSESSMENT_REVIEW.value: "Assessment Review",
    RecruitmentStage.SCREENING_PASSED.value: "Screening Passed",
    RecruitmentStage.CONTRACT_POOL.value: "Contract Pool",
    RecruitmentStage.ACTIVE.value: "Active",
    RecruitmentStage.REJECTED.value: "Rejected",
    RecruitmentStage.REPLACED.value: "Replaced",
}


def _get_candidate_visible_stage(progress: JobProgress, job: Job) -> str:
    if progress.current_stage == RecruitmentStage.PENDING_SCREENING.value:
        if job.assessment_enabled and _has_assessment_invitation(progress):
            return "assessment_file"
        return "review"
    return progress.current_stage


def _get_candidate_visible_stage_label(progress: JobProgress, visible_stage: str) -> str:
    if visible_stage == RecruitmentStage.ACTIVE.value and (progress.data or {}).get(
        JobProgressDataKey.ONBOARDING_DATE.value
    ):
        return "Successfully Onboarded"
    return CANDIDATE_VISIBLE_STAGE_LABELS.get(visible_stage, visible_stage)


def _serialize_progress_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _mark_assessment_invited(
    progress: JobProgress,
    *,
    invited_at: datetime | None = None,
    mail_task_id: int | None = None,
    sent_at: datetime | None = None,
) -> list[str]:
    next_data = dict(progress.data or {})
    changed_fields: list[str] = []
    marker_time = sent_at or invited_at or datetime.now(UTC)
    marker_value = _serialize_progress_datetime(marker_time)
    if not _normalize_text(next_data.get(JobProgressDataKey.ASSESSMENT_INVITED_AT.value)):
        next_data[JobProgressDataKey.ASSESSMENT_INVITED_AT.value] = marker_value
        changed_fields.append(JobProgressDataKey.ASSESSMENT_INVITED_AT.value)
    if sent_at is not None and next_data.get(JobProgressDataKey.ASSESSMENT_SENT_AT.value) != marker_value:
        next_data[JobProgressDataKey.ASSESSMENT_SENT_AT.value] = marker_value
        changed_fields.append(JobProgressDataKey.ASSESSMENT_SENT_AT.value)
    if (
        mail_task_id is not None
        and next_data.get(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value) != mail_task_id
    ):
        next_data[JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value] = mail_task_id
        changed_fields.append(JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value)
    if changed_fields:
        progress.data = next_data
    return changed_fields


async def _trigger_stage_mail_task(
    *,
    job: Job,
    application: CandidateApplication,
    target_stage: RecruitmentStage,
    db: AsyncSession,
    progress: JobProgress | None = None,
) -> None:
    mail_config = _get_stage_mail_config(job, target_stage)
    if mail_config is None:
        if target_stage in {RecruitmentStage.ASSESSMENT_REVIEW, RecruitmentStage.REJECTED}:
            await _record_stage_mail_operation(
                job=job,
                application=application,
                target_stage=target_stage,
                db=db,
                log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED,
                reason="mail_config_missing_or_disabled",
            )
        return

    user_result = await db.execute(
        select(User).where(
            User.id == application.user_id,
            User.is_deleted.is_(False),
        )
    )
    candidate = user_result.scalar_one_or_none()
    candidate_email = (candidate.email if candidate is not None else None) or ""
    candidate_email = candidate_email.strip()
    if not candidate_email:
        logger.warning(
            "Skip auto mail because candidate email is empty",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED,
            reason="candidate_email_empty",
        )
        return

    candidate_name = (candidate.name if candidate is not None else None) or candidate_email

    try:
        template = await get_mail_template_model(
            mail_config["template_id"],
            db,
            admin_user_id=job.owner_admin_user_id,
            include_public=True,
        )
        company_name_map = await _get_company_name_map_by_job_ids(job_ids=[job.id], db=db)
        render_context = _get_job_mail_context(job, company_name_map.get(job.id), application_id=application.id)
        if target_stage == RecruitmentStage.ASSESSMENT_REVIEW and progress is not None:
            render_context["job_progress"] = {
                "id": progress.id,
                "purpose": "assessment_invite",
            }
        render_context["candidate"] = {
            "name": candidate_name,
            "candidate_name": candidate_name,
            "email": candidate_email,
            "candidate_email": candidate_email,
        }
        mail_task = await create_mail_task(
            MailTaskCreate(
                account_id=mail_config["account_id"],
                template_id=mail_config["template_id"],
                signature_id=mail_config["signature_id"],
                subject=template.subject_template,
                body_html=template.body_html,
                to_recipients=[MailRecipient(name=candidate_name, email=candidate_email)],
                render_context=render_context,
            ),
            db,
            admin_user_id=job.owner_admin_user_id,
        )
        mail_task_id = int(mail_task.get("id") or 0) or None
        if target_stage == RecruitmentStage.ASSESSMENT_REVIEW and progress is not None:
            _mark_assessment_invited(progress, mail_task_id=mail_task_id)
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED,
            mail_task_id=mail_task_id,
        )
    except Exception:
        logger.exception(
            "Failed to create automatic stage mail task",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED,
            reason="create_mail_task_failed",
        )


async def create_job_progress_for_application(
    *,
    job: Job,
    application: CandidateApplication,
    talent_profile_id: int | None,
    field_rows: list[CandidateApplicationFieldValue],
    db: AsyncSession,
) -> JobProgress:
    final_stage, screening_mode, reason, should_send_assessment_invite = _resolve_initial_stage(
        job=job,
        field_rows=field_rows,
    )
    job_languages = _normalize_language_values((job.data or {}).get(JOB_DATA_LANGUAGES_KEY))

    progress = JobProgress(
        job_id=job.id,
        user_id=application.user_id,
        application_id=application.id,
        talent_profile_id=talent_profile_id,
        current_stage=final_stage.value,
        screening_mode=screening_mode.value,
        entered_stage_at=application.submitted_at,
        data={JobProgressDataKey.JOB_LANGUAGES.value: job_languages},
    )
    db.add(progress)
    await db.flush()

    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=job.id,
        application_id=application.id,
        talent_profile_id=talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CREATED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": job.id,
            "job_title": job.title,
            "current_stage": RecruitmentStage.PENDING_SCREENING.value,
            "current_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.PENDING_SCREENING.value),
            "screening_mode": screening_mode.value,
        },
    )

    if final_stage != RecruitmentStage.PENDING_SCREENING:
        await create_operation_log(
            db=db,
            user_id=application.user_id,
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": RecruitmentStage.PENDING_SCREENING.value,
                "from_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.PENDING_SCREENING.value),
                "to_stage": final_stage.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(final_stage.value),
                "reason": reason,
                "screening_mode": screening_mode.value,
            },
        )

    if final_stage == RecruitmentStage.REJECTED:
        await _trigger_stage_mail_task(
            job=job,
            application=application,
            target_stage=final_stage,
            db=db,
        )
    elif should_send_assessment_invite and final_stage == RecruitmentStage.PENDING_SCREENING:
        await _trigger_stage_mail_task(
            job=job,
            application=application,
            target_stage=RecruitmentStage.ASSESSMENT_REVIEW,
            db=db,
            progress=progress,
        )

    return progress


async def get_job_progress_by_application_id(
    *,
    application_id: int,
    db: AsyncSession,
) -> JobProgress | None:
    result = await db.execute(
        select(JobProgress).where(
            JobProgress.application_id == application_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def get_job_progress_models(
    *,
    job_id: int,
    progress_ids: list[int],
    db: AsyncSession,
) -> list[JobProgress]:
    result = await db.execute(
        select(JobProgress).where(
            JobProgress.job_id == job_id,
            JobProgress.id.in_(progress_ids),
            JobProgress.is_deleted.is_(False),
        )
    )
    items = result.scalars().all()
    if len(items) != len(set(progress_ids)):
        raise NotFoundException("Job progress record not found.")
    return items


def serialize_job_progress(progress: JobProgress) -> dict[str, Any]:
    return JobProgressRead(
        id=progress.id,
        job_id=progress.job_id,
        user_id=progress.user_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        screening_mode=progress.screening_mode,
        entered_stage_at=_ensure_utc_datetime(progress.entered_stage_at),
        created_at=_ensure_utc_datetime(progress.created_at),
        updated_at=_ensure_utc_datetime(progress.updated_at),
        data=_serialize_process_data(progress.data or {}, {}, exclude_contract_fields=True),
        process_assets={},
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=None,
            asset_map={},
        ),
    ).model_dump()


def _build_candidate_compensation_label(job: Job) -> str:
    if job.compensation_min is None and job.compensation_max is None:
        return "-"
    min_value = float(job.compensation_min or 0)
    max_value = float(job.compensation_max or job.compensation_min or 0)
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".")
    max_text = f"{max_value:.2f}".rstrip("0").rstrip(".")
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


def _should_show_candidate_compensation(job: Job) -> bool:
    return bool((job.data or {}).get(JOB_DATA_SHOW_COMPENSATION_KEY, True))


def _serialize_application_snapshot(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        snapshot[key] = row.display_value if row.display_value is not None else row.raw_value
    return snapshot


def _serialize_progress_process_data(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
    *,
    fallback_job_languages: list[str],
) -> dict[str, Any]:
    payload = _serialize_process_data(
        progress_data,
        asset_map,
        exclude_contract_fields=True,
    )
    if JobProgressDataKey.JOB_LANGUAGES.value not in payload:
        payload[JobProgressDataKey.JOB_LANGUAGES.value] = list(fallback_job_languages)
    else:
        payload[JobProgressDataKey.JOB_LANGUAGES.value] = _normalize_language_values(
            payload.get(JobProgressDataKey.JOB_LANGUAGES.value)
        )
    return payload


def _serialize_application_assets(
    field_rows: list[CandidateApplicationFieldValue],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for row in field_rows:
        if row.asset_id is None:
            continue
        key = row.catalog_key or row.field_key
        asset_payload = asset_map.get(int(row.asset_id))
        if asset_payload is None:
            continue
        payload[key] = {
            "asset_id": int(row.asset_id),
            "name": row.display_value or row.raw_value or asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    return payload


def _extract_process_asset_ids(progress_data: dict[str, Any]) -> list[int]:
    asset_ids: list[int] = []
    for asset_id_key in JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP.values():
        value = progress_data.get(asset_id_key.value)
        if value is None or value == "":
            continue
        try:
            asset_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    for item in _get_assessment_submission_records(progress_data):
        value = item.get("asset_id")
        if value is None or value == "":
            continue
        try:
            asset_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return asset_ids


def _get_assessment_submission_records(progress_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    if isinstance(raw_items, list):
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        if items:
            return items

    legacy_asset_id = progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value)
    legacy_name = progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT.value)
    legacy_submitted_at = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value)
    if legacy_asset_id or legacy_name:
        return [
            {
                "asset_id": legacy_asset_id,
                "name": legacy_name,
                "submitted_at": legacy_submitted_at,
            }
        ]
    return []


def _serialize_assessment_submission_records(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in _get_assessment_submission_records(progress_data):
        asset_id_value = item.get("asset_id")
        asset_id: int | None = None
        if asset_id_value is not None and asset_id_value != "":
            try:
                asset_id = int(asset_id_value)
            except (TypeError, ValueError):
                asset_id = None

        asset_payload = asset_map.get(asset_id) if asset_id is not None else None
        payload.append(
            {
                "asset_id": asset_id,
                "name": item.get("name") or (asset_payload or {}).get("original_name") or "",
                "submitted_at": item.get("submitted_at") or "",
                "preview_url": asset_payload.get("preview_url") if asset_payload else None,
                "download_url": asset_payload.get("download_url") if asset_payload else None,
                "mime_type": asset_payload.get("mime_type") if asset_payload else None,
            }
        )
    return payload


def _serialize_process_data(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
    *,
    exclude_contract_fields: bool = False,
) -> dict[str, Any]:
    payload = dict(progress_data)
    if exclude_contract_fields:
        for key in CONTRACT_PROCESS_DATA_KEYS:
            payload.pop(key, None)
    assessment_submissions = _serialize_assessment_submission_records(progress_data, asset_map)
    if assessment_submissions:
        payload[JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value] = assessment_submissions
    return payload


def _serialize_process_assets(
    progress_data: dict[str, Any],
    asset_map: dict[int, dict[str, Any]],
    *,
    exclude_contract_assets: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for file_name_key, asset_id_key in JOB_PROGRESS_ATTACHMENT_ASSET_KEY_MAP.items():
        if exclude_contract_assets and file_name_key in CONTRACT_PROCESS_ASSET_KEYS:
            continue
        asset_id_value = progress_data.get(asset_id_key.value)
        if asset_id_value is None or asset_id_value == "":
            continue
        try:
            asset_id = int(asset_id_value)
        except (TypeError, ValueError):
            continue
        asset_payload = asset_map.get(asset_id)
        if asset_payload is None:
            continue
        payload[file_name_key.value] = {
            "asset_id": asset_id,
            "name": progress_data.get(file_name_key.value) or asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    return payload


def _extract_id_attachment_asset_id(user_data: dict[str, Any] | None) -> int | None:
    payment_info = (user_data or {}).get("payment_info")
    if not isinstance(payment_info, dict):
        return None
    raw_asset_id = payment_info.get("id_attachment_asset_id")
    if raw_asset_id in (None, "", 0):
        return None
    try:
        return int(raw_asset_id)
    except (TypeError, ValueError):
        return None


async def _list_id_attachment_asset_ids_by_user(
    *,
    db: AsyncSession,
    user_ids: set[int],
) -> dict[int, int]:
    normalized_user_ids = sorted({user_id for user_id in user_ids if user_id > 0})
    if not normalized_user_ids:
        return {}
    result = await db.execute(
        select(User.id, User.data).where(
            User.id.in_(normalized_user_ids),
            User.is_deleted.is_(False),
        )
    )
    id_attachment_asset_ids: dict[int, int] = {}
    for user_id, user_data in result.all():
        asset_id = _extract_id_attachment_asset_id(user_data)
        if asset_id is not None:
            id_attachment_asset_ids[int(user_id)] = asset_id
    return id_attachment_asset_ids


def _serialize_identity_attachment_asset(
    *,
    user_id: int,
    id_attachment_asset_ids_by_user: dict[int, int],
    asset_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    asset_id = id_attachment_asset_ids_by_user.get(int(user_id))
    if asset_id is None:
        return {}
    asset_payload = asset_map.get(int(asset_id))
    if asset_payload is None:
        return {}
    return {
        "id_attachment": {
            "asset_id": int(asset_id),
            "name": asset_payload.get("original_name") or "",
            "preview_url": asset_payload.get("preview_url"),
            "download_url": asset_payload.get("download_url"),
            "mime_type": asset_payload.get("mime_type"),
        }
    }


def _extract_contract_record_asset_ids(contract_record: ContractRecord | None) -> list[int]:
    if contract_record is None:
        return []
    asset_ids = [
        contract_record.draft_contract_asset_id,
        contract_record.candidate_signed_contract_asset_id,
        contract_record.company_sealed_contract_asset_id,
        contract_record.contract_attachment_asset_id,
    ]
    return [int(asset_id) for asset_id in asset_ids if asset_id not in (None, "")]


def _build_contract_asset_read(
    *,
    asset_id: int | None,
    display_name: str | None,
    asset_map: dict[int, dict[str, Any]],
) -> JobProgressContractAssetRead | None:
    if asset_id is None:
        return None
    asset_payload = asset_map.get(asset_id)
    if asset_payload is None:
        return None
    return JobProgressContractAssetRead(
        asset_id=asset_id,
        name=display_name or asset_payload.get("original_name") or "",
        preview_url=asset_payload.get("preview_url"),
        download_url=asset_payload.get("download_url"),
        mime_type=asset_payload.get("mime_type"),
    )


def _serialize_contract_record_data(
    *,
    progress: JobProgress,
    contract_record: ContractRecord | None,
    asset_map: dict[int, dict[str, Any]],
    current_company_name: str | None = None,
    current_project_name: str | None = None,
) -> ContractRecordDataRead | None:
    contract_data = (contract_record.data or {}) if contract_record is not None else {}

    if contract_record is None:
        return None

    draft_asset_id = contract_record.draft_contract_asset_id if contract_record is not None else None

    candidate_signed_asset_id = (
        contract_record.candidate_signed_contract_asset_id if contract_record is not None else None
    )

    company_sealed_asset_id = contract_record.company_sealed_contract_asset_id if contract_record is not None else None

    contract_attachment_asset_id = contract_record.contract_attachment_asset_id if contract_record is not None else None

    if contract_record.rate is not None:
        rate = format(contract_record.rate, "f").rstrip("0").rstrip(".")
    else:
        rate = None
    if getattr(contract_record, "base_pay", None) is not None:
        base_pay = format(contract_record.base_pay, "f")
    else:
        base_pay = None

    return ContractRecordDataRead(
        id=contract_record.id,
        user_id=contract_record.user_id,
        talent_profile_id=contract_record.talent_profile_id,
        application_id=contract_record.application_id,
        job_id=contract_record.job_id,
        job_progress_id=contract_record.job_progress_id,
        service_customer_company_id=contract_record.service_customer_company_id,
        service_customer_company_name=current_company_name,
        service_customer_project_id=contract_record.service_customer_project_id,
        service_customer_project_name=current_project_name,
        agreement_ref_no=contract_record.agreement_ref_no,
        contract_status=contract_record.contract_status,
        contract_type=contract_record.contract_type,
        contractor_name=contract_record.contractor_name,
        rate=rate,
        base_pay=base_pay,
        legal_entity=contract_record.legal_entity,
        worker_type=contract_record.worker_type,
        effective_date=contract_record.effective_date,
        end_date=contract_record.end_date,
        draft_contract_attachment=_build_contract_asset_read(
            asset_id=draft_asset_id,
            display_name=(_normalize_text(contract_data.get("draft_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        candidate_signed_contract_attachment=_build_contract_asset_read(
            asset_id=candidate_signed_asset_id,
            display_name=(_normalize_text(contract_data.get("candidate_signed_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        company_sealed_contract_attachment=_build_contract_asset_read(
            asset_id=company_sealed_asset_id,
            display_name=(_normalize_text(contract_data.get("company_sealed_contract_attachment_name")) or None),
            asset_map=asset_map,
        ),
        contract_attachment=_build_contract_asset_read(
            asset_id=contract_attachment_asset_id,
            display_name=(
                _normalize_text(contract_data.get("contract_attachment_name"))
                or _normalize_text(contract_data.get("company_sealed_contract_attachment_name"))
                or _normalize_text(contract_data.get("candidate_signed_contract_attachment_name"))
                or None
            ),
            asset_map=asset_map,
        ),
        submitted_contract_at=(_normalize_text(contract_data.get("candidate_signed_contract_submitted_at")) or None),
        signing_status=_normalize_text(contract_data.get("signing_status")) or None,
        contract_review=_normalize_text(contract_data.get("contract_review")) or None,
        parse_status=contract_record.parse_status,
        parse_error=contract_record.parse_error,
        data=dict(contract_data),
    )


def _build_progress_advanced_filter_record(item: JobProgressListItemRead) -> dict[str, Any]:
    contract_record = item.contract_record_data
    assessment_submissions = item.process_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    has_latest_assessment_submission = (
        isinstance(assessment_submissions, list)
        and bool(assessment_submissions)
        and isinstance(assessment_submissions[-1], dict)
    )
    latest_assessment_submission = assessment_submissions[-1] if has_latest_assessment_submission else None
    draft_attachment_name = (
        contract_record.draft_contract_attachment.name
        if contract_record is not None and contract_record.draft_contract_attachment is not None
        else ""
    )
    candidate_signed_attachment_name = (
        contract_record.candidate_signed_contract_attachment.name
        if contract_record is not None and contract_record.candidate_signed_contract_attachment is not None
        else ""
    )
    company_sealed_attachment_name = (
        contract_record.company_sealed_contract_attachment.name
        if contract_record is not None and contract_record.company_sealed_contract_attachment is not None
        else ""
    )
    accepted_rate = _normalize_text(contract_record.rate) if contract_record is not None else ""

    return {
        **(item.application_snapshot or {}),
        "current_stage": _map_backend_stage_to_progress_stage(item.current_stage),
        "applied_at": _serialize_filter_record_datetime(item.applied_at),
        "assessment_attachment": _normalize_text(
            (latest_assessment_submission or {}).get("name")
            or item.process_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT.value)
        ),
        "assessment_sent_at": _normalize_text(
            item.process_data.get(JobProgressDataKey.ASSESSMENT_SENT_AT.value)
            or item.process_data.get(JobProgressDataKey.ASSESSMENT_INVITED_AT.value)
        ),
        "assessment_submitted_at": _normalize_text(
            (latest_assessment_submission or {}).get("submitted_at")
            or item.process_data.get(JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value)
        ),
        "assessment_result": _normalize_text(item.process_data.get(JobProgressDataKey.ASSESSMENT_RESULT.value)),
        "assessment_review_comment": _normalize_text(
            item.process_data.get(JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value)
        ),
        "assessment_reviewer": _normalize_text(item.process_data.get(JobProgressDataKey.ASSESSMENT_REVIEWER.value)),
        "qa_status": _normalize_text(item.process_data.get(JobProgressDataKey.QA_STATUS.value)),
        "qa_feedback": _normalize_text(item.process_data.get(JobProgressDataKey.QA_FEEDBACK.value)),
        "signing_status": _normalize_text(contract_record.signing_status) if contract_record is not None else "",
        "contract_number": _normalize_text(contract_record.agreement_ref_no) if contract_record is not None else "",
        "contract_draft_attachment": draft_attachment_name,
        "accepted_rate": accepted_rate,
        "effective_date": _serialize_filter_record_datetime(contract_record.effective_date)
        if contract_record is not None
        else "",
        "end_date": (
            _serialize_filter_record_datetime(contract_record.end_date) if contract_record is not None else ""
        ),
        "id_attachment": _normalize_text((item.process_assets.get("id_attachment") or {}).get("name")),
        "submitted_contract_attachment": candidate_signed_attachment_name,
        "submitted_contract_at": (
            _normalize_text(contract_record.submitted_contract_at) if contract_record is not None else ""
        ),
        "contract_review": _normalize_text(contract_record.contract_review) if contract_record is not None else "",
        "contract_return_attachment": company_sealed_attachment_name,
        "onboarding_status": (
            _normalize_text(item.process_data.get(JobProgressDataKey.ONBOARDING_STATUS.value))
            or (_normalize_text(contract_record.signing_status) if contract_record is not None else "")
        ),
        "onboarding_date": _normalize_text(item.process_data.get(JobProgressDataKey.ONBOARDING_DATE.value)),
        "gift_package_sent_at": _normalize_text(
            item.process_data.get(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
        ),
        "job_languages": " / ".join(
            _normalize_language_values(item.process_data.get(JobProgressDataKey.JOB_LANGUAGES.value))
        ),
        "rejected_from_stage": _serialize_rejected_from_stage_for_filter(item),
        "replacement_reason": _normalize_text(item.process_data.get(JobProgressDataKey.REPLACEMENT_REASON.value)),
        "note": _normalize_text(item.process_data.get(JobProgressDataKey.NOTE.value)),
    }


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
    fallback_job_languages = _normalize_language_values((job.data or {}).get(JOB_DATA_LANGUAGES_KEY)) if job else []
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
                fallback_job_languages=fallback_job_languages,
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
                    contract_company_name_map.get(contract_records[progress.id].service_customer_company_id)
                    if contract_records.get(progress.id) is not None
                    and contract_records[progress.id].service_customer_company_id is not None
                    else None
                ),
                current_project_name=(
                    contract_project_name_map.get(contract_records[progress.id].service_customer_project_id)
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
            matched_conditions.append(field_map["current_stage"].sql_expression == normalized_active_stage)
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
    contract_join_condition = (
        (ContractRecord.job_progress_id == JobProgress.id)
        & ContractRecord.is_deleted.is_(False)
        & ContractRecord.is_current.is_(True)
    )
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
    if needs_action_only:
        contract_review_expr = func.json_unquote(func.json_extract(ContractRecord.data, "$.contract_review"))
        assessment_invited_at_expr = func.json_unquote(
            func.json_extract(JobProgress.data, f"$.{JobProgressDataKey.ASSESSMENT_INVITED_AT.value}")
        )
        assessment_invite_mail_task_id_expr = func.json_unquote(
            func.json_extract(JobProgress.data, f"$.{JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value}")
        )
        conditions.append(
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.PENDING_SCREENING.value,
                    RecruitmentStage.ASSESSMENT_REVIEW.value,
                    RecruitmentStage.SCREENING_PASSED.value,
                    RecruitmentStage.CONTRACT_POOL.value,
                ]
            )
        )
        conditions.append(
            or_(
                JobProgress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value,
                and_(
                    JobProgress.current_stage == RecruitmentStage.PENDING_SCREENING.value,
                    Job.assessment_enabled.is_(True),
                    or_(
                        assessment_invited_at_expr.is_not(None),
                        assessment_invite_mail_task_id_expr.is_not(None),
                    ),
                ),
                and_(
                    JobProgress.current_stage.in_(
                        [
                            RecruitmentStage.SCREENING_PASSED.value,
                            RecruitmentStage.CONTRACT_POOL.value,
                        ]
                    ),
                    ContractRecord.id.is_not(None),
                    ContractRecord.draft_contract_asset_id.is_not(None),
                    or_(
                        ContractRecord.candidate_signed_contract_asset_id.is_(None),
                        contract_review_expr == "待修改",
                    ),
                ),
            )
        )

    count_query = (
        select(func.count())
        .select_from(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
    )
    result_query = (
        select(JobProgress, CandidateApplication, Job)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .join(Job, Job.id == JobProgress.job_id)
    )
    if needs_action_only:
        count_query = count_query.outerjoin(ContractRecord, contract_join_condition)
        result_query = result_query.outerjoin(ContractRecord, contract_join_condition)

    total_result = await db.execute(count_query.where(*conditions))
    total = int(total_result.scalar() or 0)
    if total == 0:
        return CandidateJobApplicationListPage(items=[], total=0, page=page, page_size=page_size).model_dump()

    result = await db.execute(
        result_query.where(*conditions)
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()
    if not rows:
        return CandidateJobApplicationListPage(items=[], total=total, page=page, page_size=page_size).model_dump()

    application_ids = [application.id for _, application, _ in rows]
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

    contract_records = await list_current_contract_records_by_progress_ids(
        progress_ids=[progress.id for progress, _, _ in rows],
        db=db,
    )

    asset_ids = {int(row.asset_id) for row in field_rows if row.asset_id is not None}
    for progress, _, _ in rows:
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
            assessment_enabled=job.assessment_enabled,
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
        for progress, application, job in rows
    ]
    return CandidateJobApplicationListPage(items=items, total=total, page=page, page_size=page_size).model_dump()


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
        or_(
            ContractRecord.draft_contract_asset_id.is_not(None),
            ContractRecord.candidate_signed_contract_asset_id.is_not(None),
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
            contract_record_data=_serialize_contract_record_data(
                progress=progress,
                contract_record=contract_record,
                asset_map=asset_map,
                current_company_name=None,
                current_project_name=None,
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


async def move_job_progress_stage(  # noqa: C901
    *,
    job_id: int,
    progress_ids: list[int],
    target_stage: str,
    admin_user_id: int,
    db: AsyncSession,
    reason: str | None = None,
    reviewer_scope_admin_user_id: int | None = None,
) -> dict[str, Any]:
    try:
        normalized_target_stage = RecruitmentStage(target_stage)
    except Exception as exc:
        raise BadRequestException("Unsupported target stage.") from exc

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    application_ids = [progress.application_id for progress in progress_items]
    application_map: dict[int, CandidateApplication] = {}
    if application_ids:
        application_result = await db.execute(
            select(CandidateApplication).where(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        application_map = {int(application.id): application for application in application_result.scalars().all()}

    active_contract_record_map: dict[int, ContractRecord] = {}
    leaving_active_contract_record_map: dict[int, ContractRecord] = {}

    for progress in progress_items:
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")
        allowed_targets = get_allowed_recruitment_stage_transitions(
            progress.current_stage,
            assessment_enabled=job.assessment_enabled,
        )
        if normalized_target_stage not in allowed_targets:
            raise BadRequestException(
                f"Current stage {progress.current_stage} cannot move to {normalized_target_stage.value}."
            )
        if (
            progress.current_stage == RecruitmentStage.ASSESSMENT_REVIEW.value
            and normalized_target_stage == RecruitmentStage.SCREENING_PASSED
        ):
            if not _has_assessment_attachment(progress):
                raise BadRequestException("Screening passed stage requires an assessment submission.")
            assessment_result = _normalize_text((progress.data or {}).get(JobProgressDataKey.ASSESSMENT_RESULT.value))
            if assessment_result not in {"通过", "待定"}:
                raise BadRequestException("Screening passed stage requires assessment result 通过 or 待定.")
        if (
            progress.current_stage == RecruitmentStage.SCREENING_PASSED.value
            and normalized_target_stage == RecruitmentStage.ASSESSMENT_REVIEW
        ):
            qa_status = _normalize_text((progress.data or {}).get(JobProgressDataKey.QA_STATUS.value))
            if qa_status != "待返修":
                raise BadRequestException("Only QA rework records can move back to assessment review.")

        if normalized_target_stage == RecruitmentStage.ACTIVE:
            contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)
            if contract_record is None:
                raise BadRequestException("Active stage requires a contract record.")
            if contract_record.candidate_signed_contract_asset_id in (None, 0, ""):
                raise BadRequestException("Active stage requires a candidate signed contract.")
            current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
            if current_contract_review != "审核通过":
                raise BadRequestException("Active stage requires an approved contract review.")
            active_contract_record_map[progress.id] = contract_record
        if (
            progress.current_stage == RecruitmentStage.CONTRACT_POOL.value
            and normalized_target_stage == RecruitmentStage.SCREENING_PASSED
        ):
            contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)
            if contract_record is not None and (
                contract_record.company_sealed_contract_asset_id not in (None, 0, "")
                or contract_record.contract_status == "Active"
            ):
                raise BadRequestException("Signed active contracts cannot move back to screening passed.")
        if progress.current_stage == RecruitmentStage.ACTIVE.value and normalized_target_stage in {
            RecruitmentStage.REPLACED,
            RecruitmentStage.REJECTED,
        }:
            contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)
            if contract_record is None:
                raise BadRequestException("Leaving active stage requires a contract record.")
            leaving_active_contract_record_map[progress.id] = contract_record

    for progress in progress_items:
        from_stage = progress.current_stage
        next_data = dict(progress.data or {})
        if normalized_target_stage == RecruitmentStage.REJECTED:
            next_data[JobProgressDataKey.REJECTED_FROM_STAGE.value] = from_stage
        elif JobProgressDataKey.REJECTED_FROM_STAGE.value in next_data:
            next_data.pop(JobProgressDataKey.REJECTED_FROM_STAGE.value, None)
        if normalized_target_stage == RecruitmentStage.SCREENING_PASSED:
            next_data.pop(JobProgressDataKey.QA_STATUS.value, None)
        if normalized_target_stage == RecruitmentStage.CONTRACT_POOL:
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "可发合同"
        if normalized_target_stage == RecruitmentStage.ACTIVE:
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"

        progress.current_stage = normalized_target_stage.value
        progress.entered_stage_at = datetime.now(UTC)
        progress.data = next_data

        if normalized_target_stage == RecruitmentStage.ACTIVE:
            contract_record = active_contract_record_map[progress.id]
            contract_record.contract_status = "Active"
            contract_record.updated_by_admin_user_id = admin_user_id
            await ensure_user_referral_profile_from_job(
                user_id=int(progress.user_id),
                job=job,
                db=db,
                admin_user_id=admin_user_id,
                contract_record=contract_record,
            )
        if progress.id in leaving_active_contract_record_map:
            contract_record = leaving_active_contract_record_map[progress.id]
            contract_record.contract_status = "Terminated"
            contract_record.end_date = contract_record.end_date or get_default_contract_end_date(
                contract_record.effective_date or datetime.now(UTC).date()
            )
            contract_record.updated_by_admin_user_id = admin_user_id

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": from_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(from_stage),
                "to_stage": normalized_target_stage.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(normalized_target_stage.value),
                "operator_admin_user_id": admin_user_id,
                "reason": reason or "",
            },
        )

        should_trigger_stage_mail = (
            normalized_target_stage == RecruitmentStage.REJECTED and from_stage != RecruitmentStage.ACTIVE.value
        )
        if should_trigger_stage_mail:
            application = application_map.get(int(progress.application_id))
            if application is not None:
                await _trigger_stage_mail_task(
                    job=job,
                    application=application,
                    target_stage=normalized_target_stage,
                    db=db,
                )

    await db.flush()
    return {
        "updated_count": len(progress_items),
        "target_stage": normalized_target_stage.value,
        "target_stage_cn_name": get_recruitment_stage_cn_name(normalized_target_stage.value),
    }


async def execute_job_progress_assessment_automation(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    reviewer_scope_admin_user_id: int | None = None,
) -> dict[str, Any]:
    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)

    passed_ids: list[int] = []
    rejected_ids: list[int] = []
    untouched_ids: list[int] = []
    missing_attachment_ids: list[int] = []
    missing_result_ids: list[int] = []

    for progress in progress_items:
        if progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can execute automation.")
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")

        if not _has_assessment_attachment(progress):
            untouched_ids.append(progress.id)
            missing_attachment_ids.append(progress.id)
            continue

        assessment_result = _normalize_text((progress.data or {}).get(JobProgressDataKey.ASSESSMENT_RESULT.value))
        if assessment_result in {"通过", "待定"}:
            passed_ids.append(progress.id)
        elif assessment_result == "不通过":
            rejected_ids.append(progress.id)
        else:
            untouched_ids.append(progress.id)
            missing_result_ids.append(progress.id)

    if passed_ids:
        await move_job_progress_stage(
            job_id=job_id,
            progress_ids=passed_ids,
            target_stage=RecruitmentStage.SCREENING_PASSED.value,
            admin_user_id=admin_user_id,
            db=db,
            reason="assessment_automation_passed",
            reviewer_scope_admin_user_id=reviewer_scope_admin_user_id,
        )
    if rejected_ids:
        await move_job_progress_stage(
            job_id=job_id,
            progress_ids=rejected_ids,
            target_stage=RecruitmentStage.REJECTED.value,
            admin_user_id=admin_user_id,
            db=db,
            reason="assessment_automation_rejected",
            reviewer_scope_admin_user_id=reviewer_scope_admin_user_id,
        )

    return {
        "passed_count": len(passed_ids),
        "rejected_count": len(rejected_ids),
        "untouched_count": len(untouched_ids),
        "missing_attachment_count": len(missing_attachment_ids),
        "missing_result_count": len(missing_result_ids),
    }


async def mark_job_progress_assessment_invited(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    mail_task_id: int | None = None,
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    allowed_stages = {
        RecruitmentStage.PENDING_SCREENING.value,
        RecruitmentStage.ASSESSMENT_REVIEW.value,
    }
    invalid_progress = next(
        (progress for progress in progress_items if progress.current_stage not in allowed_stages),
        None,
    )
    if invalid_progress is not None:
        raise BadRequestException("Assessment invite can only be marked before screening is passed.")

    changed_count = 0
    updated_field_keys: set[str] = set()
    now = datetime.now(UTC)
    for progress in progress_items:
        changed_fields = _mark_assessment_invited(
            progress,
            invited_at=now,
            mail_task_id=mail_task_id,
            sent_at=sent_at,
        )
        if not changed_fields:
            continue
        changed_count += 1
        updated_field_keys.update(changed_fields)
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": progress.job_id,
                "target_stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                "target_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ASSESSMENT_REVIEW.value),
                "reason": "assessment_invite_marked",
                "mail_task_id": mail_task_id,
                "operator_admin_user_id": admin_user_id,
            },
        )

    await db.flush()
    return JobProgressAssessmentInviteMarkResponse(
        updated_count=changed_count,
        updated_field_keys=sorted(updated_field_keys),
    ).model_dump()


async def sync_assessment_sent_at_from_mail_task(mail_task_id: int) -> bool:
    async with local_session() as db:
        task = await db.get(MailTask, mail_task_id)
        if task is None or task.status != MailTaskStatus.SENT.value or task.sent_at is None:
            return False

        task_data = task.data or {}
        render_context = task_data.get(MAIL_TASK_DATA_RENDER_CONTEXT_KEY, {}) if isinstance(task_data, dict) else {}
        job_progress_context = render_context.get("job_progress", {}) if isinstance(render_context, dict) else {}
        progress: JobProgress | None = None
        if isinstance(job_progress_context, dict) and job_progress_context.get("purpose") == "assessment_invite":
            raw_progress_id = job_progress_context.get("id")
            try:
                progress_id = int(raw_progress_id)
            except (TypeError, ValueError):
                progress_id = 0
            if progress_id:
                progress = await db.get(JobProgress, progress_id)

        if progress is None:
            mail_task_id_expr = _build_progress_json_text_expression(
                JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value
            )
            progress_result = await db.execute(
                select(JobProgress)
                .where(
                    JobProgress.is_deleted.is_(False),
                    mail_task_id_expr == str(mail_task_id),
                )
                .limit(1)
            )
            progress = progress_result.scalar_one_or_none()

        if progress is None or progress.is_deleted:
            return False

        changed_fields = _mark_assessment_invited(progress, sent_at=task.sent_at, mail_task_id=mail_task_id)
        if not changed_fields:
            return False
        await db.commit()
        return True


async def update_job_progress_assessment_review(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    reviewer_scope_admin_user_id: int | None = None,
    assessment_result: str | None = None,
    assessment_review_comment: str | None = None,
    assessment_reviewer: str | None = None,
    assessment_reviewer_admin_user_id: int | None = None,
    qa_status: str | None = None,
    qa_feedback: str | None = None,
) -> dict[str, Any]:
    assessment_field_updates: dict[JobProgressDataKey, Any] = {}
    reviewer_field_updates: dict[JobProgressDataKey, Any] = {}
    qa_field_updates: dict[JobProgressDataKey, Any] = {}
    if assessment_result is not None:
        assessment_field_updates[JobProgressDataKey.ASSESSMENT_RESULT] = assessment_result
    if assessment_review_comment is not None:
        assessment_field_updates[JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT] = assessment_review_comment
    if assessment_reviewer is not None:
        reviewer_field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER] = assessment_reviewer
    if assessment_reviewer_admin_user_id is not None:
        reviewer_field_updates[JobProgressDataKey.ASSESSMENT_REVIEWER_ADMIN_USER_ID] = assessment_reviewer_admin_user_id
    if qa_status is not None:
        qa_field_updates[JobProgressDataKey.QA_STATUS] = qa_status
    if qa_feedback is not None:
        qa_field_updates[JobProgressDataKey.QA_FEEDBACK] = qa_feedback

    field_updates = {**assessment_field_updates, **reviewer_field_updates, **qa_field_updates}

    if not field_updates:
        raise BadRequestException("At least one review field is required.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    candidate_users: dict[int, User] = {}
    if progress_items:
        user_result = await db.execute(
            select(User).where(
                User.id.in_([progress.user_id for progress in progress_items]),
                User.is_deleted.is_(False),
            )
        )
        candidate_users = {item.id: item for item in user_result.scalars().all()}
    sender_admin_name: str | None = None
    sender_result = await db.execute(
        select(AdminUser).where(
            AdminUser.id == admin_user_id,
            AdminUser.is_deleted.is_(False),
        )
    )
    sender_admin = sender_result.scalar_one_or_none()
    if sender_admin is not None:
        sender_admin_name = sender_admin.name

    assessment_field_key_values = {key.value for key in assessment_field_updates}
    reviewer_field_key_values = {key.value for key in reviewer_field_updates}
    qa_field_key_values = {key.value for key in qa_field_updates}

    for progress in progress_items:
        if assessment_field_updates and progress.current_stage not in {
            RecruitmentStage.ASSESSMENT_REVIEW.value,
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.REJECTED.value,
        }:
            raise BadRequestException(
                "Only assessment review, screening passed, or rejected stage records can update review fields here."
            )
        if reviewer_field_updates and progress.current_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
            raise BadRequestException("Only assessment review stage records can update reviewer fields here.")
        if qa_field_updates and progress.current_stage not in {
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.REJECTED.value,
        }:
            raise BadRequestException("Only screening passed or rejected stage records can update QA here.")
        if (
            reviewer_scope_admin_user_id is not None
            and progress.assessment_reviewer_admin_user_id != reviewer_scope_admin_user_id
        ):
            raise NotFoundException("Job progress record not found.")

    updated_field_keys = [key.value for key in field_updates]
    for progress in progress_items:
        next_data = dict(progress.data or {})
        changed_fields: dict[str, dict[str, Any]] = {}
        for field_key, next_value in field_updates.items():
            previous_value = next_data.get(field_key.value)
            if previous_value == next_value:
                continue
            next_data[field_key.value] = next_value
            changed_fields[field_key.value] = {
                "from": previous_value,
                "to": next_value,
            }

        if not changed_fields:
            continue

        progress.data = next_data
        if JobProgressDataKey.ASSESSMENT_REVIEWER_ADMIN_USER_ID in field_updates:
            progress.assessment_reviewer_admin_user_id = assessment_reviewer_admin_user_id
            progress.assessment_assigned_at = datetime.now(UTC)

        if "assessment_reviewer_admin_user_id" in changed_fields and assessment_reviewer_admin_user_id is not None:
            candidate = candidate_users.get(progress.user_id)
            candidate_name = (
                (candidate.name if candidate is not None else None)
                or (candidate.email if candidate is not None else None)
                or f"候选人#{progress.user_id}"
            )
            await create_admin_internal_notification(
                db=db,
                recipient_admin_user_id=assessment_reviewer_admin_user_id,
                sender_admin_user_id=admin_user_id,
                category="assessment_assignment",
                title="收到新的测试题判题任务",
                description=f"已将 {candidate_name} 的测试题分配到您这边，请及时完成评审。",
                action_url=f"/jobs/{job.id}/progress?stage=assessment&candidateId={progress.user_id}",
                data={
                    "job_id": job.id,
                    "job_title": job.title,
                    "progress_id": progress.id,
                    "candidate_user_id": progress.user_id,
                    "application_id": progress.application_id,
                    "stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                    "sender_name": sender_admin_name,
                    "candidate_name": candidate_name,
                },
            )

        assessment_changed_fields = {
            key: value for key, value in changed_fields.items() if key in assessment_field_key_values
        }
        reviewer_changed_fields = {
            key: value for key, value in changed_fields.items() if key in reviewer_field_key_values
        }
        qa_changed_fields = {key: value for key, value in changed_fields.items() if key in qa_field_key_values}

        if assessment_changed_fields or reviewer_changed_fields:
            await create_operation_log(
                db=db,
                user_id=progress.user_id,
                job_id=progress.job_id,
                application_id=progress.application_id,
                talent_profile_id=progress.talent_profile_id,
                log_type=OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
                data={
                    "job_progress_id": progress.id,
                    "job_id": job.id,
                    "job_title": job.title,
                    "current_stage": progress.current_stage,
                    "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                    "operator_admin_user_id": admin_user_id,
                    "updated_fields": {
                        **assessment_changed_fields,
                        **reviewer_changed_fields,
                    },
                },
            )

        if qa_changed_fields:
            await create_operation_log(
                db=db,
                user_id=progress.user_id,
                job_id=progress.job_id,
                application_id=progress.application_id,
                talent_profile_id=progress.talent_profile_id,
                log_type=OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value,
                data={
                    "job_progress_id": progress.id,
                    "job_id": job.id,
                    "job_title": job.title,
                    "current_stage": progress.current_stage,
                    "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                    "operator_admin_user_id": admin_user_id,
                    "updated_fields": qa_changed_fields,
                },
            )

    await db.flush()
    return {
        "updated_count": len(progress_items),
        "updated_field_keys": updated_field_keys,
    }


async def update_job_progress_note(
    *,
    job_id: int,
    progress_ids: list[int],
    note: str | None,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    normalized_note = (note or "").strip()
    changed_count = 0

    for progress in progress_items:
        next_data = dict(progress.data or {})
        previous_value = _normalize_text(next_data.get(JobProgressDataKey.NOTE.value))
        if previous_value == normalized_note:
            continue

        if normalized_note:
            next_data[JobProgressDataKey.NOTE.value] = normalized_note
        else:
            next_data.pop(JobProgressDataKey.NOTE.value, None)
        progress.data = next_data
        changed_count += 1

        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_NOTE_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "updated_fields": {
                    JobProgressDataKey.NOTE.value: {
                        "from": previous_value,
                        "to": normalized_note,
                    },
                },
            },
        )

    await db.flush()
    return {
        "updated_count": changed_count,
        "updated_field_keys": [JobProgressDataKey.NOTE.value],
    }


def _format_current_process_datetime() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def update_job_progress_onboarding(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    onboarding_status: str | None = None,
    onboarding_date: date | None = None,
    update_onboarding_status: bool = False,
    update_onboarding_date: bool = False,
) -> dict[str, Any]:
    has_onboarding_status_update = update_onboarding_status or onboarding_status is not None
    has_onboarding_date_update = update_onboarding_date or onboarding_date is not None
    if not has_onboarding_status_update and not has_onboarding_date_update:
        raise BadRequestException("At least one onboarding field is required.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    allowed_stages = {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
        RecruitmentStage.ACTIVE.value,
        RecruitmentStage.REPLACED.value,
        RecruitmentStage.REJECTED.value,
    }
    invalid_progress = next(
        (progress for progress in progress_items if progress.current_stage not in allowed_stages),
        None,
    )
    if invalid_progress is not None:
        raise BadRequestException("Onboarding fields can only be updated after screening is passed.")

    changed_count = 0
    updated_field_keys: set[str] = set()
    normalized_onboarding_status = onboarding_status.strip() or None if onboarding_status is not None else None
    milestone_timestamp = (
        _format_current_process_datetime()
        if normalized_onboarding_status in {"已进群", "已发大礼包"}
        else None
    )
    for progress in progress_items:
        next_data = dict(progress.data or {})
        changed_fields: dict[str, dict[str, Any]] = {}
        if has_onboarding_status_update:
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_STATUS.value)
            if previous_value != normalized_onboarding_status:
                if normalized_onboarding_status is None:
                    next_data.pop(JobProgressDataKey.ONBOARDING_STATUS.value, None)
                else:
                    next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = normalized_onboarding_status
                changed_fields[JobProgressDataKey.ONBOARDING_STATUS.value] = {
                    "from": previous_value,
                    "to": normalized_onboarding_status,
                }
        if has_onboarding_date_update:
            next_date = onboarding_date.isoformat() if onboarding_date is not None else None
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_DATE.value)
            if previous_value != next_date:
                if next_date is None:
                    next_data.pop(JobProgressDataKey.ONBOARDING_DATE.value, None)
                else:
                    next_data[JobProgressDataKey.ONBOARDING_DATE.value] = next_date
                changed_fields[JobProgressDataKey.ONBOARDING_DATE.value] = {
                    "from": previous_value,
                    "to": next_date,
                }
        if milestone_timestamp and normalized_onboarding_status == "已进群":
            previous_value = next_data.get(JobProgressDataKey.ONBOARDING_DATE.value)
            if previous_value != milestone_timestamp:
                next_data[JobProgressDataKey.ONBOARDING_DATE.value] = milestone_timestamp
                changed_fields[JobProgressDataKey.ONBOARDING_DATE.value] = {
                    "from": previous_value,
                    "to": milestone_timestamp,
                }
        if milestone_timestamp and normalized_onboarding_status == "已发大礼包":
            previous_value = next_data.get(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
            if previous_value != milestone_timestamp:
                next_data[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = milestone_timestamp
                changed_fields[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] = {
                    "from": previous_value,
                    "to": milestone_timestamp,
                }
        if not changed_fields:
            continue
        progress.data = next_data
        changed_count += 1
        updated_field_keys.update(changed_fields.keys())
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_NOTE_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "updated_fields": changed_fields,
            },
        )

    await db.flush()
    return JobProgressOnboardingUpdateResponse(
        updated_count=changed_count,
        updated_field_keys=sorted(updated_field_keys),
    ).model_dump()


async def update_job_progress_contract_record(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    ensure_contract_record: bool = False,
    agreement_ref_no: str | None = None,
    signing_status: str | None = None,
    contract_review: str | None = None,
    rate: str | None = None,
    end_date: date | None = None,
    update_agreement_ref_no: bool = False,
    update_signing_status: bool = False,
    update_contract_review: bool = False,
    update_rate: bool = False,
    update_end_date: bool = False,
) -> dict[str, Any]:
    changed_fields: list[str] = []
    field_updates: dict[str, Any] = {}
    data_updates: dict[str, Any] = {}
    has_agreement_ref_no_update = update_agreement_ref_no or agreement_ref_no is not None
    has_rate_update = update_rate or rate is not None
    has_signing_status_update = update_signing_status or signing_status is not None
    has_contract_review_update = update_contract_review or contract_review is not None
    has_end_date_update = update_end_date or end_date is not None

    if has_agreement_ref_no_update:
        field_updates["agreement_ref_no"] = (agreement_ref_no or "").strip() or None
        changed_fields.append("agreement_ref_no")
    if has_rate_update:
        field_updates["rate"] = _normalize_decimal(rate)
        changed_fields.append("rate")
    if has_signing_status_update:
        data_updates["signing_status"] = (signing_status or "").strip() or None
        changed_fields.append("signing_status")
    if has_contract_review_update:
        data_updates["contract_review"] = (contract_review or "").strip() or None
        changed_fields.append("contract_review")
    if has_end_date_update:
        field_updates["end_date"] = end_date
        changed_fields.append("end_date")

    if not changed_fields and not ensure_contract_record:
        raise BadRequestException("At least one contract field is required.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    updated_contract_records: dict[int, ContractRecord] = {}
    for progress in progress_items:
        if ensure_contract_record and progress.current_stage not in {
            RecruitmentStage.SCREENING_PASSED.value,
            RecruitmentStage.CONTRACT_POOL.value,
        }:
            raise BadRequestException("Contract record can only be supplemented in 筛选通过 or 合同库.")
        _validate_contract_record_update_stage(
            stage=progress.current_stage,
            changed_fields=changed_fields,
        )
        if data_updates.get("contract_review") == "审核通过":
            current_contract_record = await get_current_contract_record_by_progress_id(
                progress_id=progress.id,
                db=db,
            )
            if current_contract_record is None or current_contract_record.candidate_signed_contract_asset_id in (
                None,
                0,
                "",
            ):
                raise BadRequestException("Approved contract review requires a candidate signed contract.")

        contract_record = await upsert_contract_record_for_progress(
            progress=progress,
            job=job,
            db=db,
            admin_user_id=admin_user_id,
            field_updates=field_updates,
            data_updates=data_updates,
        )
        if data_updates.get("contract_review") == "审核通过":
            previous_stage = progress.current_stage
            activated_at = datetime.now(UTC)
            next_data = dict(progress.data or {})
            next_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"
            progress.data = next_data
            progress.current_stage = RecruitmentStage.ACTIVE.value
            progress.entered_stage_at = activated_at
            contract_record.contract_status = CONTRACT_STATUS_ACTIVE
            contract_record.updated_by_admin_user_id = admin_user_id
            await ensure_user_referral_profile_from_job(
                user_id=int(progress.user_id),
                job=job,
                db=db,
                admin_user_id=admin_user_id,
                contract_record=contract_record,
            )
            await create_operation_log(
                db=db,
                user_id=progress.user_id,
                job_id=progress.job_id,
                application_id=progress.application_id,
                talent_profile_id=progress.talent_profile_id,
                log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
                data={
                    "job_progress_id": progress.id,
                    "job_id": job.id,
                    "job_title": job.title,
                    "from_stage": previous_stage,
                    "from_stage_cn_name": get_recruitment_stage_cn_name(previous_stage),
                    "to_stage": RecruitmentStage.ACTIVE.value,
                    "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ACTIVE.value),
                    "operator_admin_user_id": admin_user_id,
                    "reason": "contract_review_approved",
                },
            )
        updated_contract_records[progress.id] = contract_record
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": changed_fields,
                "contract_record_ensured": ensure_contract_record,
            },
        )

    asset_ids: set[int] = set()
    for record in updated_contract_records.values():
        asset_ids.update(_extract_contract_record_asset_ids(record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    await db.flush()
    return JobProgressContractRecordUpdateResponse(
        updated_count=len(progress_items),
        updated_field_keys=changed_fields,
        items=[
            JobProgressContractRecordUpdateItemRead(
                progress_id=progress.id,
                contract_record_data=_serialize_contract_record_data(
                    progress=progress,
                    contract_record=updated_contract_records.get(progress.id),
                    asset_map=asset_map,
                ),
            )
            for progress in progress_items
        ],
    ).model_dump()


async def notify_job_progress_sign_contract(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    account_id: int,
    template_id: int | None,
    signature_id: int | None,
    subject: str,
    body_html: str,
    cc_recipients: list[MailRecipient],
    bcc_recipients: list[MailRecipient],
    attachment_asset_ids: list[int],
    render_context: dict[str, Any],
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    invalid_stage = next(
        (progress for progress in progress_items if progress.current_stage != RecruitmentStage.SCREENING_PASSED.value),
        None,
    )
    if invalid_stage is not None:
        raise BadRequestException("Sign contract notification can only be sent in 筛选通过.")

    contract_record_map = await list_current_contract_records_by_progress_ids(
        progress_ids=[progress.id for progress in progress_items],
        db=db,
    )
    missing_contract = next(
        (
            progress
            for progress in progress_items
            if progress.id not in contract_record_map
            or contract_record_map[progress.id].draft_contract_asset_id in (None, 0, "")
        ),
        None,
    )
    if missing_contract is not None:
        raise BadRequestException("Sign contract notification requires uploaded draft contracts.")

    user_result = await db.execute(
        select(User).where(
            User.id.in_([progress.user_id for progress in progress_items]),
            User.is_deleted.is_(False),
        )
    )
    user_map = {int(user.id): user for user in user_result.scalars().all()}
    missing_email = next(
        (
            progress
            for progress in progress_items
            if not ((user_map.get(progress.user_id).email if user_map.get(progress.user_id) else "") or "").strip()
        ),
        None,
    )
    if missing_email is not None:
        raise BadRequestException("Candidate email is required for sign contract notification.")

    if not _contains_contract_upload_url_variable(body_html):
        raise BadRequestException("Sign contract notification template must include {{contract_upload_url}}.")

    company_name_map = await _get_company_name_map_by_job_ids(job_ids=[job.id], db=db)
    base_render_context = {
        **_get_job_mail_context(job, company_name_map.get(job.id)),
        **(render_context or {}),
    }

    mail_task_ids: list[int] = []
    updated_contract_records: dict[int, ContractRecord] = {}
    for progress in progress_items:
        candidate = user_map[progress.user_id]
        candidate_email = (candidate.email or "").strip()
        candidate_name = (candidate.name or candidate_email).strip()
        contract_upload_url = _build_candidate_contract_upload_url(progress.application_id)
        task = await create_mail_task(
            MailTaskCreate(
                account_id=account_id,
                template_id=template_id,
                signature_id=signature_id,
                subject=subject,
                body_html=body_html,
                to_recipients=[MailRecipient(name=candidate_name, email=candidate_email)],
                cc_recipients=cc_recipients,
                bcc_recipients=bcc_recipients,
                attachment_asset_ids=list(dict.fromkeys(int(asset_id) for asset_id in attachment_asset_ids)),
                render_context={
                    **base_render_context,
                    "candidate_name": candidate_name,
                    "candidate_email": candidate_email,
                    "contract_upload_url": contract_upload_url,
                    "candidate": {
                        "name": candidate_name,
                        "candidate_name": candidate_name,
                        "email": candidate_email,
                        "candidate_email": candidate_email,
                    },
                    "contract": {
                        "upload_url": contract_upload_url,
                        "contract_upload_url": contract_upload_url,
                    },
                },
            ),
            db,
            admin_user_id=admin_user_id,
            commit=False,
            dispatch_event=False,
        )
        mail_task_id = int(task.get("id") or 0)
        if mail_task_id:
            mail_task_ids.append(mail_task_id)

        next_progress_data = dict(progress.data or {})
        next_progress_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "已通知人选签合同"
        progress.data = next_progress_data

        updated_contract_record = await upsert_contract_record_for_progress(
            progress=progress,
            job=job,
            db=db,
            admin_user_id=admin_user_id,
            data_updates={"signing_status": "已通知人选签合同"},
        )
        updated_contract_records[progress.id] = updated_contract_record
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": ["signing_status"],
                "progress_updated_fields": [JobProgressDataKey.ONBOARDING_STATUS.value],
                "mail_task_id": mail_task_id or None,
            },
        )

    asset_ids: set[int] = set()
    for record in updated_contract_records.values():
        asset_ids.update(_extract_contract_record_asset_ids(record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    await db.flush()
    await db.commit()
    for mail_task_id in mail_task_ids:
        await dispatch_mail_task_created_event(mail_task_id, db, admin_user_id=admin_user_id)

    return JobProgressNotifySignContractResponse(
        updated_count=len(progress_items),
        mail_task_ids=mail_task_ids,
        items=[
            JobProgressContractRecordUpdateItemRead(
                progress_id=progress.id,
                contract_record_data=_serialize_contract_record_data(
                    progress=progress,
                    contract_record=updated_contract_records.get(progress.id),
                    asset_map=asset_map,
                ),
            )
            for progress in progress_items
        ],
    ).model_dump()


async def submit_job_progress_assessment(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    assessment_suffix = Path((upload.filename or "").strip()).suffix.lower()
    if assessment_suffix not in {".xls", ".xlsx"}:
        raise BadRequestException("Only Excel files (.xls, .xlsx) are accepted for assessment uploads.")

    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    if not job.assessment_enabled:
        raise BadRequestException("This job does not accept assessment uploads.")

    progress_result = await db.execute(
        select(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.user_id == user_id,
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.PENDING_SCREENING.value,
                    RecruitmentStage.ASSESSMENT_REVIEW.value,
                ]
            ),
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Assessment upload record not found for this job.")
    if progress.current_stage == RecruitmentStage.PENDING_SCREENING.value and not _has_assessment_invitation(progress):
        raise BadRequestException("Assessment upload is available after the assessment invitation is sent.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="user",
            owner_id=user_id,
        ),
        upload=upload,
    )
    submitted_at = datetime.now(UTC)
    previous_stage = progress.current_stage
    next_data = dict(progress.data or {})
    submission_records = _get_assessment_submission_records(next_data)
    submission_records.append(
        {
            "asset_id": int(asset_payload["id"]),
            "name": asset_payload["original_name"],
            "submitted_at": submitted_at.isoformat(),
        }
    )
    next_data[JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value] = submission_records
    next_data[JobProgressDataKey.ASSESSMENT_ATTACHMENT.value] = asset_payload["original_name"]
    next_data[JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value] = int(asset_payload["id"])
    next_data[JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value] = submitted_at.isoformat()
    next_data.pop(JobProgressDataKey.ASSESSMENT_RESULT.value, None)
    next_data.pop(JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value, None)
    next_data.pop(JobProgressDataKey.QA_STATUS.value, None)
    progress.data = next_data

    if previous_stage != RecruitmentStage.ASSESSMENT_REVIEW.value:
        progress.current_stage = RecruitmentStage.ASSESSMENT_REVIEW.value
        progress.entered_stage_at = submitted_at
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": progress.job_id,
                "application_id": progress.application_id,
                "from_stage": previous_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(previous_stage),
                "to_stage": RecruitmentStage.ASSESSMENT_REVIEW.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ASSESSMENT_REVIEW.value),
                "reason": "候选人上传测试题，自动进入测试题回收。",
                "screening_mode": progress.screening_mode,
            },
        )

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "previous_stage": previous_stage,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "assessment_asset_id": int(asset_payload["id"]),
            "assessment_attachment": asset_payload["original_name"],
            "assessment_submitted_at": next_data[JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value],
            "assessment_submission_count": len(submission_records),
        },
    )

    await db.flush()

    serialized_asset = {
        "asset_id": int(asset_payload["id"]),
        "name": asset_payload["original_name"],
        "preview_url": asset_payload["preview_url"],
        "download_url": asset_payload["download_url"],
        "mime_type": asset_payload["mime_type"],
    }
    return JobProgressAssessmentUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        assessment_asset=asset_payload,
        process_data=_serialize_process_data(next_data, {int(asset_payload["id"]): asset_payload}),
        process_assets={JobProgressDataKey.ASSESSMENT_ATTACHMENT.value: serialized_asset},
    ).model_dump()


async def submit_job_progress_candidate_signed_contract(
    *,
    job_id: int,
    user_id: int,
    upload: UploadFile,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress)
        .join(CandidateApplication, CandidateApplication.id == JobProgress.application_id)
        .where(
            JobProgress.job_id == job_id,
            JobProgress.user_id == user_id,
            JobProgress.current_stage.in_(
                [
                    RecruitmentStage.SCREENING_PASSED.value,
                    RecruitmentStage.CONTRACT_POOL.value,
                ]
            ),
            JobProgress.is_deleted.is_(False),
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(JobProgress.entered_stage_at.desc(), JobProgress.id.desc())
        .limit(1)
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Signed contract upload record not found for this job.")

    file_name = (upload.filename or "").strip().lower()
    if not file_name.endswith((".doc", ".docx")):
        raise BadRequestException("Signed contract must be uploaded as a .doc or .docx file.")

    progress_data = dict(progress.data or {})
    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)
    if contract_record is None or contract_record.draft_contract_asset_id in (None, "", 0):
        raise BadRequestException("Draft contract is not available yet.")
    if contract_record.contract_status in {CONTRACT_STATUS_TERMINATED, CONTRACT_STATUS_EXPIRED}:
        raise BadRequestException("Contract signing is no longer available because this contract is inactive.")

    current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
    if (
        progress.current_stage == RecruitmentStage.CONTRACT_POOL.value
        and contract_record.candidate_signed_contract_asset_id not in (None, "", 0)
        and current_contract_review != "待修改"
    ):
        raise BadRequestException(
            "Your signed contract is currently under review. "
            "You can upload a new version after the review status changes to Needs Revision."
        )

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="user",
            owner_id=user_id,
        ),
        upload=upload,
    )

    submitted_at = datetime.now(UTC)
    from_stage = progress.current_stage
    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "submitted_contract_asset_id": int(asset_payload["id"]),
            "submitted_contract_attachment": asset_payload["original_name"],
            "submitted_contract_at": submitted_at.isoformat(),
        },
    )

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        field_updates={
            "candidate_signed_contract_asset_id": int(asset_payload["id"]),
            "parse_status": "pending",
            "parse_error": None,
        },
        data_updates={
            "source": "single_signed_upload",
            "candidate_signed_contract_attachment_name": asset_payload["original_name"],
            "candidate_signed_contract_submitted_at": submitted_at.isoformat(),
            "contract_review": "待审核",
        },
    )

    if from_stage == RecruitmentStage.SCREENING_PASSED.value:
        progress.current_stage = RecruitmentStage.CONTRACT_POOL.value
        progress.entered_stage_at = submitted_at
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": from_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(from_stage),
                "to_stage": RecruitmentStage.CONTRACT_POOL.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.CONTRACT_POOL.value),
                "reason": "candidate_signed_contract_submitted",
            },
        )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCandidateSignedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        candidate_signed_contract_asset=asset_payload,
        process_data=_serialize_process_data(progress_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()


async def upload_job_progress_contract_draft(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress).where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.SCREENING_PASSED.value,
        RecruitmentStage.CONTRACT_POOL.value,
    }:
        raise BadRequestException("Contract draft can only be uploaded in 筛选通过 or 合同库.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )

    current_process_data = dict(progress.data or {})

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "contract_draft_asset_id": int(asset_payload["id"]),
            "contract_draft_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )

    uploaded_at = datetime.now(UTC)
    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates={
            "draft_contract_asset_id": int(asset_payload["id"]),
            "effective_date": uploaded_at.date(),
        },
        data_updates={
            "source": "single_draft_upload",
            "draft_contract_attachment_name": asset_payload["original_name"],
            "draft_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )

    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressContractDraftUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        contract_draft_asset=asset_payload,
        process_data=_serialize_process_data(current_process_data, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(current_process_data, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()


async def upload_job_progress_company_sealed_contract(
    *,
    job_id: int,
    progress_id: int,
    upload: UploadFile,
    admin_user_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_result = await db.execute(
        select(JobProgress).where(
            JobProgress.id == progress_id,
            JobProgress.job_id == job_id,
            JobProgress.is_deleted.is_(False),
        )
    )
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise NotFoundException("Job progress not found.")
    if progress.current_stage not in {
        RecruitmentStage.CONTRACT_POOL.value,
        RecruitmentStage.ACTIVE.value,
    }:
        raise BadRequestException("Company signed contract can only be uploaded in 合同库 or Active.")

    contract_record = await get_current_contract_record_by_progress_id(progress_id=progress.id, db=db)
    if contract_record is None:
        raise BadRequestException("Company signed contract requires a contract record.")
    if contract_record.candidate_signed_contract_asset_id in (None, 0, ""):
        raise BadRequestException(
            "Company signed contract can only be uploaded after the candidate signed contract is submitted."
        )

    current_contract_review = _normalize_text((contract_record.data or {}).get("contract_review"))
    if current_contract_review != "审核通过":
        raise BadRequestException("Company signed contract can only be uploaded after contract review is approved.")

    asset_payload = await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module="job_progress",
            owner_type="job_progress",
            owner_id=progress.id,
        ),
        upload=upload,
    )
    uploaded_at = datetime.now(UTC)
    from_stage = progress.current_stage

    await create_operation_log(
        db=db,
        user_id=progress.user_id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        talent_profile_id=progress.talent_profile_id,
        log_type=OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
        data={
            "job_progress_id": progress.id,
            "job_id": progress.job_id,
            "application_id": progress.application_id,
            "current_stage": progress.current_stage,
            "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
            "operator_admin_user_id": admin_user_id,
        },
    )

    field_updates: dict[str, Any] = {
        "company_sealed_contract_asset_id": int(asset_payload["id"]),
        "contract_attachment_asset_id": int(asset_payload["id"]),
    }
    if contract_record.effective_date is None:
        field_updates["effective_date"] = uploaded_at.date()

    contract_record = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        field_updates=field_updates,
        data_updates={
            "source": "single_company_sealed_upload",
            "company_sealed_contract_attachment_name": asset_payload["original_name"],
            "company_sealed_contract_uploaded_at": uploaded_at.isoformat(),
        },
    )
    next_progress_data = dict(progress.data or {})
    next_progress_data[JobProgressDataKey.ONBOARDING_STATUS.value] = "成功签约"
    progress.data = next_progress_data
    if progress.current_stage != RecruitmentStage.ACTIVE.value:
        progress.current_stage = RecruitmentStage.ACTIVE.value
        progress.entered_stage_at = uploaded_at
    contract_record.contract_status = CONTRACT_STATUS_ACTIVE
    contract_record.updated_by_admin_user_id = admin_user_id
    await ensure_user_referral_profile_from_job(
        user_id=int(progress.user_id),
        job=job,
        db=db,
        admin_user_id=admin_user_id,
        contract_record=contract_record,
    )
    if from_stage != RecruitmentStage.ACTIVE.value:
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "from_stage": from_stage,
                "from_stage_cn_name": get_recruitment_stage_cn_name(from_stage),
                "to_stage": RecruitmentStage.ACTIVE.value,
                "to_stage_cn_name": get_recruitment_stage_cn_name(RecruitmentStage.ACTIVE.value),
                "reason": "company_sealed_contract_uploaded",
                "operator_admin_user_id": admin_user_id,
            },
        )
    await create_candidate_internal_notification(
        db=db,
        recipient_user_id=progress.user_id,
        sender_admin_user_id=admin_user_id,
        category="contract_company_signed",
        title="Your contract is ready",
        description=f"The company countersigned contract for {job.title} is ready. You can view it in My Contracts.",
        action_url=f"/my-contracts/{progress.application_id}",
        data={
            "job_id": job.id,
            "job_title": job.title,
            "job_progress_id": progress.id,
            "application_id": progress.application_id,
            "contract_record_id": contract_record.id,
            "company_sealed_contract_asset_id": int(asset_payload["id"]),
            "company_sealed_contract_attachment": asset_payload["original_name"],
        },
    )
    await db.flush()

    contract_asset_map = {int(asset_payload["id"]): asset_payload}
    if contract_record is not None:
        contract_asset_ids = _extract_contract_record_asset_ids(contract_record)
        if contract_asset_ids:
            asset_result = await db.execute(
                select(Asset).where(
                    Asset.id.in_(sorted(set(contract_asset_ids))),
                    Asset.is_deleted.is_(False),
                )
            )
            contract_asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}
    return JobProgressCompanySealedContractUploadResponse(
        job_progress_id=progress.id,
        job_id=progress.job_id,
        application_id=progress.application_id,
        current_stage=progress.current_stage,
        current_stage_cn_name=get_recruitment_stage_cn_name(progress.current_stage),
        company_sealed_contract_asset=asset_payload,
        process_data=_serialize_process_data(progress.data or {}, {}, exclude_contract_fields=True),
        process_assets=_serialize_process_assets(progress.data or {}, {}, exclude_contract_assets=True),
        contract_record_data=_serialize_contract_record_data(
            progress=progress,
            contract_record=contract_record,
            asset_map=contract_asset_map,
        ),
    ).model_dump()
