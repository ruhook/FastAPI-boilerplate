from decimal import Decimal
from typing import Any

from .const import (
    JOB_DATA_APPLICATION_SUMMARY_KEY,
    JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY,
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_COLLABORATORS_KEY,
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_HIGHLIGHTS_KEY,
    JOB_DATA_LANGUAGES_KEY,
    JOB_DATA_PUBLISH_CHECKLIST_KEY,
    JOB_DATA_REJECTION_MAIL_CONFIG_KEY,
    JOB_DATA_SCREENING_RULES_KEY,
    JOB_DATA_SHOW_COMPENSATION_KEY,
)
from .model import Job
from .policy import can_edit_job
from .schema import (
    JobAssessmentConfig,
    JobAutomationRuleGroup,
    JobCreate,
    JobFormStrategy,
    JobRead,
    JobRejectionMailConfig,
    JobUpdate,
)


def _normalize_decimal(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _build_compensation_label(job: Job) -> str:
    min_value = _normalize_decimal(job.compensation_min)
    max_value = _normalize_decimal(job.compensation_max)
    if min_value is None and max_value is None:
        return "-"
    min_text = f"{min_value:.2f}".rstrip("0").rstrip(".") if min_value is not None else "0"
    max_source = max_value if max_value is not None else min_value
    max_text = f"{max_source:.2f}".rstrip("0").rstrip(".") if max_source is not None else "0"
    return f"USD {min_text} - {max_text} {job.compensation_unit}"


def _build_rejection_mail_config(data: dict[str, Any]) -> JobRejectionMailConfig:
    raw = data.get(JOB_DATA_REJECTION_MAIL_CONFIG_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return JobRejectionMailConfig(
        enabled=bool(raw.get("enabled", False)),
        mail_account_id=raw.get("mail_account_id"),
        mail_template_id=raw.get("mail_template_id"),
        mail_signature_id=raw.get("mail_signature_id"),
        mail_account_label=raw.get("mail_account_label"),
        mail_template_name=raw.get("mail_template_name"),
        mail_signature_name=raw.get("mail_signature_name"),
    )


def _job_data_from_payload(
    payload: JobCreate,
    *,
    owner_name: str | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if owner_name is not None:
        data["owner_name"] = owner_name
    data[JOB_DATA_COLLABORATORS_KEY] = payload.collaborators
    data[JOB_DATA_LANGUAGES_KEY] = payload.languages
    data[JOB_DATA_HIGHLIGHTS_KEY] = payload.highlights
    data[JOB_DATA_FORM_FIELDS_KEY] = [field.model_dump() for field in payload.form_fields]
    data[JOB_DATA_AUTOMATION_RULES_KEY] = payload.automation_rules.model_dump()
    data[JOB_DATA_SCREENING_RULES_KEY] = payload.screening_rules
    data[JOB_DATA_PUBLISH_CHECKLIST_KEY] = payload.publish_checklist
    data[JOB_DATA_APPLICATION_SUMMARY_KEY] = (
        payload.application_summary.model_dump() if payload.application_summary else None
    )
    data[JOB_DATA_SHOW_COMPENSATION_KEY] = payload.show_compensation
    data[JOB_DATA_CONTRACT_EXAMPLE_KEY] = payload.contract_example or ""
    return data


def _merge_job_data(
    current_data: dict[str, Any],
    payload: JobUpdate,
    *,
    owner_name: str | None,
) -> dict[str, Any]:
    next_data = dict(current_data or {})
    if owner_name is not None:
        next_data["owner_name"] = owner_name
    if payload.collaborators is not None:
        next_data[JOB_DATA_COLLABORATORS_KEY] = payload.collaborators
    if payload.languages is not None:
        next_data[JOB_DATA_LANGUAGES_KEY] = payload.languages
    if payload.highlights is not None:
        next_data[JOB_DATA_HIGHLIGHTS_KEY] = payload.highlights
    if payload.form_fields is not None:
        next_data[JOB_DATA_FORM_FIELDS_KEY] = [field.model_dump() for field in payload.form_fields]
    if payload.automation_rules is not None:
        next_data[JOB_DATA_AUTOMATION_RULES_KEY] = payload.automation_rules.model_dump()
    if payload.screening_rules is not None:
        next_data[JOB_DATA_SCREENING_RULES_KEY] = payload.screening_rules
    if payload.publish_checklist is not None:
        next_data[JOB_DATA_PUBLISH_CHECKLIST_KEY] = payload.publish_checklist
    if payload.application_summary is not None:
        next_data[JOB_DATA_APPLICATION_SUMMARY_KEY] = payload.application_summary.model_dump()
    if payload.show_compensation is not None:
        next_data[JOB_DATA_SHOW_COMPENSATION_KEY] = payload.show_compensation
    if payload.contract_example is not None:
        next_data[JOB_DATA_CONTRACT_EXAMPLE_KEY] = payload.contract_example
    return next_data


def serialize_job(
    job: Job,
    owner_name: str | None,
    company_name: str,
    project_name: str,
    referral_bonus_model_name: str | None = None,
    *,
    current_admin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = job.data or {}
    assessment_config = JobAssessmentConfig(
        enabled=job.assessment_enabled,
        mail_account_id=job.assessment_mail_account_id,
        mail_template_id=job.assessment_mail_template_id,
        mail_signature_id=job.assessment_mail_signature_id,
        mail_account_label=data.get("assessment_mail_account_label"),
        mail_template_name=data.get("assessment_mail_template_name"),
        mail_signature_name=data.get("assessment_mail_signature_name"),
        assessment_external_url=data.get(JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY) or data.get("assessment_link"),
    )
    rejection_mail_config = _build_rejection_mail_config(data)
    return JobRead(
        id=job.id,
        title=job.title,
        company=company_name,
        company_id=job.company_id,
        project=project_name,
        project_id=job.project_id,
        referral_bonus_model_id=job.referral_bonus_model_id,
        referral_bonus_model_name=referral_bonus_model_name,
        country=job.country,
        status=job.status,
        work_mode=job.work_mode,
        languages=list(data.get(JOB_DATA_LANGUAGES_KEY) or []),
        compensation_min=job.compensation_min,
        compensation_max=job.compensation_max,
        compensation_unit=job.compensation_unit,
        show_compensation=bool(data.get(JOB_DATA_SHOW_COMPENSATION_KEY, True)),
        description=job.description,
        contract_example=data.get(JOB_DATA_CONTRACT_EXAMPLE_KEY) or "",
        owner_name=owner_name or data.get("owner_name"),
        collaborators=list(data.get(JOB_DATA_COLLABORATORS_KEY) or []),
        form_strategy=JobFormStrategy(
            template_id=job.form_template_id,
        ),
        assessment_config=assessment_config,
        rejection_mail_config=rejection_mail_config,
        form_fields=list(data.get(JOB_DATA_FORM_FIELDS_KEY) or []),
        automation_rules=JobAutomationRuleGroup.model_validate(
            data.get(JOB_DATA_AUTOMATION_RULES_KEY) or {"combinator": "and", "rules": []}
        ),
        screening_rules=list(data.get(JOB_DATA_SCREENING_RULES_KEY) or []),
        publish_checklist=list(data.get(JOB_DATA_PUBLISH_CHECKLIST_KEY) or []),
        highlights=list(data.get(JOB_DATA_HIGHLIGHTS_KEY) or []),
        application_summary=data.get(JOB_DATA_APPLICATION_SUMMARY_KEY),
        applicant_count=job.applicant_count,
        owner_admin_user_id=job.owner_admin_user_id,
        can_edit=can_edit_job(job, current_admin),
        created_at=job.created_at,
        updated_at=job.updated_at,
        data=data,
    ).model_dump()
