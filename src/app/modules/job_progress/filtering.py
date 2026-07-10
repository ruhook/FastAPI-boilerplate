from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select

from ...core.advanced_filter import AdvancedFilterFieldDefinition
from ..candidate_application.model import CandidateApplication
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..contract_record.model import ContractRecord
from ..job.const import JOB_DATA_FORM_FIELDS_KEY
from ..job.model import Job
from ..user.model import User
from .const import JobProgressDataKey, RecruitmentStage
from .language_rules import DEFAULT_PROGRESS_LANGUAGE, normalize_progress_language_value
from .model import JobProgress
from .normalization import _ensure_utc_datetime, _normalize_text
from .schema import JobProgressListItemRead

ADVANCED_FILTER_BACKEND_STAGE_MAP: dict[str, str] = {
    "screening": RecruitmentStage.PENDING_SCREENING.value,
    "assessment": RecruitmentStage.ASSESSMENT_REVIEW.value,
    "passed": RecruitmentStage.SCREENING_PASSED.value,
    "contract": RecruitmentStage.CONTRACT_POOL.value,
    "employed": RecruitmentStage.ACTIVE.value,
    "replaced": RecruitmentStage.REPLACED.value,
    "eliminated": RecruitmentStage.REJECTED.value,
}


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


def _build_job_languages_sql_expression():
    snapshot_expr = _build_progress_json_text_expression(JobProgressDataKey.JOB_LANGUAGES.value)
    snapshot_text = func.replace(func.replace(func.replace(snapshot_expr, '"', ""), "[", ""), "]", "")
    first_legacy_value = func.trim(func.substring_index(snapshot_text, ",", 1))
    return func.coalesce(func.nullif(first_legacy_value, ""), DEFAULT_PROGRESS_LANGUAGE)


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
        "salary_confirmed_at": "date",
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
        elif name == "salary_confirmed_at":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.SALARY_CONFIRMED_AT.value)
        elif name == "gift_package_sent_at":
            sql_expression = _build_progress_json_text_expression(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)
        elif name == "job_languages":
            sql_expression = _build_job_languages_sql_expression()
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
        "salary_confirmed_at": _normalize_text(item.process_data.get(JobProgressDataKey.SALARY_CONFIRMED_AT.value)),
        "gift_package_sent_at": _normalize_text(item.process_data.get(JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value)),
        "job_languages": normalize_progress_language_value(
            item.process_data.get(JobProgressDataKey.JOB_LANGUAGES.value)
        ),
        "rejected_from_stage": _serialize_rejected_from_stage_for_filter(item),
        "replacement_reason": _normalize_text(item.process_data.get(JobProgressDataKey.REPLACEMENT_REASON.value)),
        "note": _normalize_text(item.process_data.get(JobProgressDataKey.NOTE.value)),
    }
