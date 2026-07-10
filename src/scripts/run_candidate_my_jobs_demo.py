import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from httpx import ASGITransport
from sqlalchemy import or_, select

from ..app.core.db.database import local_session
from ..app.main_admin import app as admin_app
from ..app.main_web import app as web_app
from ..app.modules.admin.mail_task.model import MailTask
from ..app.modules.assets.schema import AssetUploadPayload
from ..app.modules.assets.service import create_asset_from_bytes
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.candidate_field.const import CandidateFieldKey
from ..app.modules.contract_record.model import ContractRecord
from ..app.modules.job.const import (
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JobStatus,
)
from ..app.modules.job.model import Job
from ..app.modules.job_progress.model import JobProgress
from .run_client_apply_demo import (
    ensure_resume_asset,
    fetch_current_user,
    login_candidate,
    register_or_reuse_candidate,
    submit_application,
)
from .run_client_assessment_upload_demo import (
    build_demo_docx_bytes,
    build_demo_pdf_bytes,
    build_demo_xlsx_bytes,
    upload_assessment,
)
from .seed_apply_demo_flow import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_ADMIN_USERNAME,
    build_contract_example_html,
    ensure_admin_user,
    ensure_company,
    ensure_company_project,
    ensure_dictionary,
    ensure_form_template,
    ensure_referral_bonus_model,
    ensure_role,
)
from .seed_candidate_base_form_template import DICTIONARY_DEFINITIONS
from .seed_job_progress_demo_flow import (
    ensure_assessment_mail_dependencies,
    ensure_rejection_mail_dependencies,
)

WEB_BASE_URL = "http://testserver/api/v1"
ADMIN_BASE_URL = "http://testserver/api/v1"
DEFAULT_CANDIDATE_NAME = "Ruan Hao Kang"
DEFAULT_CANDIDATE_EMAIL = "712696307@qq.com"
DEFAULT_CANDIDATE_PASSWORD = "12345678"
CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY = "candidate_portal_demo_case_key"
LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX = "Candidate Portal Demo - "
CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION = (
    "<p>负责葡萄牙语数据标注、内容质量检查与结果反馈，按照项目规范完成交付，并与项目团队保持及时沟通。</p>"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare candidate-facing Applications demo data.")
    parser.add_argument("--candidate-name", default=DEFAULT_CANDIDATE_NAME, help="Candidate display name.")
    parser.add_argument("--candidate-email", default=DEFAULT_CANDIDATE_EMAIL, help="Candidate email.")
    parser.add_argument("--candidate-password", default=DEFAULT_CANDIDATE_PASSWORD, help="Candidate password.")
    return parser.parse_args()


def print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def print_detail(message: str) -> None:
    print(f"  - {message}")


def should_auto_apply(definition: dict[str, Any]) -> bool:
    return bool(definition.get("auto_apply", True))


def ensure_ok(response: httpx.Response, message: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{message}: {response.status_code} {response.text}")
    return response.json()


def build_rule(*, field_key: CandidateFieldKey, operator: str, value: str, field_type: str = "text") -> dict[str, Any]:
    return {
        "fieldKey": field_key.value,
        "fieldLabel": field_key.value,
        "fieldType": field_type,
        "operator": operator,
        "value": value,
    }


def build_rule_group(*rules: dict[str, Any], combinator: str = "and") -> dict[str, Any]:
    return {"combinator": combinator, "rules": list(rules)}


PORTAL_JOB_DEFINITIONS = [
    {
        "key": "fresh_apply_flow",
        "title": "待申请",
        "company_name": "TMX Fresh Flow Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("9.50"),
        "compensation_max": Decimal("13.50"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="bachelor_completed",
            )
        ),
        "application_scenario": "assessment_manual_pending",
        "target_stage": "assessment_review",
        "auto_apply": False,
    },
    {
        "key": "application_review",
        "title": "申请审核中",
        "company_name": "TMX Application Review Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("8.00"),
        "compensation_max": Decimal("12.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": {"combinator": "and", "rules": []},
        "application_scenario": "assessment_manual_pending",
        "target_stage": "pending_screening",
        "auto_apply": True,
    },
    {
        "key": "assessment_action_required",
        "title": "待上传测试题",
        "company_name": "TMX Assessment Action Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("9.00"),
        "compensation_max": Decimal("13.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="master_completed",
            )
        ),
        "application_scenario": "assessment_manual_pending",
        "target_stage": "pending_screening",
        "auto_apply": True,
    },
    {
        "key": "assessment_under_review",
        "title": "测试题审核中",
        "company_name": "TMX Assessment Review Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("10.00"),
        "compensation_max": Decimal("14.00"),
        "compensation_unit": "Per Day",
        "assessment_enabled": True,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="master_completed",
            )
        ),
        "application_scenario": "assessment_manual_pending",
        "target_stage": "assessment_review",
        "auto_apply": True,
    },
    {
        "key": "rate_confirmation_waiting",
        "title": "费率确认待通知",
        "company_name": "TMX Rate Waiting Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("10.50"),
        "compensation_max": Decimal("14.50"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="master_completed",
            )
        ),
        "application_scenario": "assessment_manual_pending",
        "assessment_submission_file_name": "rate-confirmation-waiting.xlsx",
        "target_stage": "screening_passed",
        "auto_apply": True,
    },
    {
        "key": "rate_confirmation_action_required",
        "title": "待查看费率说明",
        "company_name": "TMX Rate Action Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("11.00"),
        "compensation_max": Decimal("15.00"),
        "compensation_unit": "Per Line",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "application_scenario": "no_assessment_auto_pass",
        "target_stage": "screening_passed",
        "auto_apply": True,
    },
    {
        "key": "signed_contract_action_required",
        "title": "待上传签署合同",
        "company_name": "TMX Contract Action Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("12.00"),
        "compensation_max": Decimal("16.00"),
        "compensation_unit": "Per Month",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "application_scenario": "no_assessment_auto_pass",
        "target_stage": "screening_passed",
        "auto_apply": True,
    },
    {
        "key": "signed_contract_under_review",
        "title": "合同审核中",
        "company_name": "TMX Contract Review Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("12.50"),
        "compensation_max": Decimal("16.50"),
        "compensation_unit": "Per Month",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "application_scenario": "no_assessment_auto_pass",
        "target_stage": "contract_pool",
        "auto_apply": True,
    },
    {
        "key": "task_group_action_required",
        "title": "待查看入组说明",
        "company_name": "TMX Task Group Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("13.00"),
        "compensation_max": Decimal("17.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "application_scenario": "no_assessment_auto_pass",
        "target_stage": "active",
        "auto_apply": True,
    },
    {
        "key": "successfully_onboarded",
        "title": "已成功入职",
        "company_name": "TMX Onboarded Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("13.50"),
        "compensation_max": Decimal("17.50"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "application_scenario": "no_assessment_auto_pass",
        "target_stage": "active",
        "auto_apply": True,
    },
    {
        "key": "rejected",
        "title": "已拒绝（申请审核阶段）",
        "company_name": "TMX Reject Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
        "compensation_min": Decimal("7.00"),
        "compensation_max": Decimal("10.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": False,
        "automation_rules": build_rule_group(
            build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="phd",
            )
        ),
        "application_scenario": "no_assessment_auto_rejected",
        "target_stage": "rejected",
        "auto_apply": True,
    },
]

PORTAL_JOB_DEFINITIONS.extend(
    [
        {
            "key": "assessment_revision_required",
            "title": "测试题待重新提交",
            "company_name": "TMX Assessment Revision Lab",
            "country": "Brazil",
            "work_mode": "Remote",
            "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
            "compensation_min": Decimal("10.25"),
            "compensation_max": Decimal("14.25"),
            "compensation_unit": "Per Hour",
            "assessment_enabled": True,
            "automation_rules": {"combinator": "and", "rules": []},
            "application_scenario": "assessment_manual_pending",
            "target_stage": "assessment_review",
            "auto_apply": True,
        },
        {
            "key": "signed_contract_revision_required",
            "title": "合同待重新提交",
            "company_name": "TMX Contract Revision Lab",
            "country": "Brazil",
            "work_mode": "Remote",
            "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
            "compensation_min": Decimal("12.75"),
            "compensation_max": Decimal("16.75"),
            "compensation_unit": "Per Month",
            "assessment_enabled": False,
            "automation_rules": build_rule_group(
                build_rule(
                    field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                    operator="contains",
                    value="Brazil",
                )
            ),
            "application_scenario": "no_assessment_auto_pass",
            "target_stage": "contract_pool",
            "auto_apply": True,
        },
        {
            "key": "onboarding_preparation",
            "title": "入职准备中",
            "company_name": "TMX Onboarding Preparation Lab",
            "country": "Brazil",
            "work_mode": "Remote",
            "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
            "compensation_min": Decimal("13.25"),
            "compensation_max": Decimal("17.25"),
            "compensation_unit": "Per Hour",
            "assessment_enabled": False,
            "automation_rules": build_rule_group(
                build_rule(
                    field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                    operator="contains",
                    value="Brazil",
                )
            ),
            "application_scenario": "no_assessment_auto_pass",
            "target_stage": "active",
            "auto_apply": True,
        },
        {
            "key": "rejected_late_stage",
            "title": "已拒绝（合同阶段）",
            "company_name": "TMX Late Rejection Lab",
            "country": "Brazil",
            "work_mode": "Remote",
            "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
            "compensation_min": Decimal("11.75"),
            "compensation_max": Decimal("15.75"),
            "compensation_unit": "Per Hour",
            "assessment_enabled": False,
            "automation_rules": build_rule_group(
                build_rule(
                    field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                    operator="contains",
                    value="Brazil",
                )
            ),
            "application_scenario": "no_assessment_auto_pass",
            "target_stage": "rejected",
            "auto_apply": True,
        },
        {
            "key": "engagement_ended",
            "title": "合作已结束",
            "company_name": "TMX Engagement End Lab",
            "country": "Brazil",
            "work_mode": "Remote",
            "description": CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION,
            "compensation_min": Decimal("13.75"),
            "compensation_max": Decimal("17.75"),
            "compensation_unit": "Per Hour",
            "assessment_enabled": False,
            "automation_rules": build_rule_group(
                build_rule(
                    field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                    operator="contains",
                    value="Brazil",
                )
            ),
            "application_scenario": "no_assessment_auto_pass",
            "target_stage": "replaced",
            "auto_apply": True,
        },
    ]
)

EXPECTED_CANDIDATE_VIEW_BY_KEY: dict[str, dict[str, Any]] = {
    "application_review": {
        "candidate_status": "under_review",
        "candidate_stage": "application_review",
        "candidate_action": "view_details",
        "candidate_action_required": False,
    },
    "assessment_action_required": {
        "candidate_status": "action_required",
        "candidate_stage": "assessment_file",
        "candidate_action": "upload_assessment",
        "candidate_action_required": True,
    },
    "assessment_revision_required": {
        "candidate_status": "action_required",
        "candidate_stage": "assessment_file",
        "candidate_action": "upload_assessment",
        "candidate_action_required": True,
    },
    "assessment_under_review": {
        "candidate_status": "under_review",
        "candidate_stage": "assessment_file",
        "candidate_action": "view_status",
        "candidate_action_required": False,
    },
    "rate_confirmation_waiting": {
        "candidate_status": "under_review",
        "candidate_stage": "rate_confirmation",
        "candidate_action": "view_status",
        "candidate_action_required": False,
    },
    "rate_confirmation_action_required": {
        "candidate_status": "action_required",
        "candidate_stage": "rate_confirmation",
        "candidate_action": "view_rate_instructions",
        "candidate_action_required": True,
    },
    "signed_contract_action_required": {
        "candidate_status": "action_required",
        "candidate_stage": "signed_contract",
        "candidate_action": "upload_contract",
        "candidate_action_required": True,
    },
    "signed_contract_revision_required": {
        "candidate_status": "action_required",
        "candidate_stage": "signed_contract",
        "candidate_action": "upload_contract",
        "candidate_action_required": True,
    },
    "signed_contract_under_review": {
        "candidate_status": "under_review",
        "candidate_stage": "signed_contract",
        "candidate_action": "view_status",
        "candidate_action_required": False,
    },
    "onboarding_preparation": {
        "candidate_status": "under_review",
        "candidate_stage": "task_group",
        "candidate_action": "view_status",
        "candidate_action_required": False,
    },
    "task_group_action_required": {
        "candidate_status": "action_required",
        "candidate_stage": "task_group",
        "candidate_action": "view_joining_instructions",
        "candidate_action_required": True,
    },
    "successfully_onboarded": {
        "candidate_status": "onboarded",
        "candidate_stage": "onboarding_completed",
        "candidate_action": "view_status",
        "candidate_action_required": False,
    },
    "rejected": {
        "candidate_status": "rejected",
        "candidate_stage": "application_review",
        "candidate_action": "view_details",
        "candidate_action_required": False,
    },
    "rejected_late_stage": {
        "candidate_status": "rejected",
        "candidate_stage": "signed_contract",
        "candidate_action": "view_details",
        "candidate_action_required": False,
    },
    "engagement_ended": {
        "candidate_status": "engagement_ended",
        "candidate_stage": "onboarding_completed",
        "candidate_action": "view_details",
        "candidate_action_required": False,
    },
}


def build_expected_candidate_portal_cases() -> list[dict[str, Any]]:
    return [
        {
            "key": str(definition["key"]),
            "title": str(definition["title"]),
            "expected_candidate_view": dict(EXPECTED_CANDIDATE_VIEW_BY_KEY[str(definition["key"])]),
        }
        for definition in PORTAL_JOB_DEFINITIONS
        if should_auto_apply(definition)
    ]


def build_expected_candidate_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    contract_uploads = sum(case["expected_candidate_view"]["candidate_action"] == "upload_contract" for case in cases)
    other_actions = sum(
        case["expected_candidate_view"]["candidate_action_required"]
        and case["expected_candidate_view"]["candidate_action"] != "upload_contract"
        for case in cases
    )
    total_action_required = contract_uploads + other_actions
    return {
        "contract_uploads": contract_uploads,
        "other_actions": other_actions,
        "monitoring": len(cases) - total_action_required,
        "total_action_required": total_action_required,
    }


def build_candidate_summary_from_items(items: list[dict[str, Any]]) -> dict[str, int]:
    contract_uploads = sum(item.get("candidate_action") == "upload_contract" for item in items)
    other_actions = sum(
        bool(item.get("candidate_action_required")) and item.get("candidate_action") != "upload_contract"
        for item in items
    )
    total_action_required = contract_uploads + other_actions
    return {
        "contract_uploads": contract_uploads,
        "other_actions": other_actions,
        "monitoring": len(items) - total_action_required,
        "total_action_required": total_action_required,
    }


def get_expected_action_required_case_keys(cases: list[dict[str, Any]]) -> set[str]:
    return {str(case["key"]) for case in cases if case["expected_candidate_view"]["candidate_action_required"]}


def get_candidate_portal_demo_case_key(job: Any) -> str:
    data = job.data if isinstance(getattr(job, "data", None), dict) else {}
    return str(data.get(CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY) or "")


def is_candidate_portal_demo_owned_job(job: Any) -> bool:
    return bool(get_candidate_portal_demo_case_key(job)) or str(getattr(job, "title", "") or "").startswith(
        LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX
    )


def is_current_candidate_portal_demo_job(job: Any) -> bool:
    current_keys = {str(item["key"]) for item in PORTAL_JOB_DEFINITIONS}
    return get_candidate_portal_demo_case_key(job) in current_keys


def should_verify_auto_assessment_mail_task() -> bool:
    return any(
        should_auto_apply(definition) and definition.get("application_scenario") == "assessment_auto_pass"
        for definition in PORTAL_JOB_DEFINITIONS
    )


def build_application_items(
    *,
    scenario_key: str,
    candidate_name: str,
    candidate_email: str,
    resume_asset_id: int,
) -> list[dict[str, Any]]:
    if scenario_key == "assessment_auto_pass":
        education_status = "master_completed"
        education_display = "master_completed"
        additional_information = "Candidate portal demo assessment review scenario."
        whatsapp = "+55 11 92000 0001"
    elif scenario_key == "assessment_manual_pending":
        education_status = "bachelor_completed"
        education_display = "bachelor_completed"
        additional_information = "Candidate portal demo pending screening scenario."
        whatsapp = "+55 11 92000 0002"
    elif scenario_key == "no_assessment_auto_pass":
        education_status = "bachelor_completed"
        education_display = "bachelor_completed"
        additional_information = "Candidate portal demo positive automation scenario."
        whatsapp = "+55 11 92000 0003"
    elif scenario_key == "no_assessment_auto_rejected":
        education_status = "bachelor_completed"
        education_display = "bachelor_completed"
        additional_information = "Candidate portal demo rejected scenario."
        whatsapp = "+55 11 92000 0004"
    else:
        raise ValueError(f"Unsupported scenario key: {scenario_key}")

    return [
        {"field_key": CandidateFieldKey.FULL_NAME.value, "value": candidate_name},
        {"field_key": CandidateFieldKey.EMAIL.value, "value": candidate_email},
        {"field_key": CandidateFieldKey.WHATSAPP.value, "value": whatsapp},
        {"field_key": CandidateFieldKey.COUNTRY_OF_RESIDENCE.value, "value": "Brazil"},
        {"field_key": CandidateFieldKey.CITY.value, "value": "Sao Paulo"},
        {"field_key": CandidateFieldKey.NATIONALITY.value, "value": "Brazil"},
        {"field_key": CandidateFieldKey.NATIVE_LANGUAGES.value, "value": "Portuguese"},
        {"field_key": CandidateFieldKey.ADDITIONAL_LANGUAGES.value, "value": "English"},
        {
            "field_key": CandidateFieldKey.ENGLISH_PROFICIENCY.value,
            "value": "fully_professional_proficiency",
            "display_value": "fully_professional_proficiency",
        },
        {"field_key": CandidateFieldKey.AGE_RANGE.value, "value": "26_30", "display_value": "26_30"},
        {
            "field_key": CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value,
            "value": "4_8_hours",
            "display_value": "4_8_hours",
        },
        {
            "field_key": CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value,
            "value": "yes",
            "display_value": "yes",
        },
        {
            "field_key": CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value,
            "value": "6_10",
            "display_value": "6_10",
        },
        {
            "field_key": CandidateFieldKey.EDUCATION_STATUS.value,
            "value": education_status,
            "display_value": education_display,
        },
        {
            "field_key": CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE.value,
            "value": "1_2_years",
            "display_value": "1_2_years",
        },
        {
            "field_key": CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP.value,
            "value": "no_sponsorship_required",
            "display_value": "no_sponsorship_required",
        },
        {
            "field_key": CandidateFieldKey.RESUME_ATTACHMENT.value,
            "value": "candidate-my-jobs-resume.pdf",
            "display_value": "candidate-my-jobs-resume.pdf",
            "asset_id": resume_asset_id,
        },
        {
            "field_key": CandidateFieldKey.JOB_SOURCE.value,
            "value": "linkedin_job_post",
            "display_value": "linkedin_job_post",
        },
        {
            "field_key": CandidateFieldKey.ADDITIONAL_INFORMATION.value,
            "value": additional_information,
        },
    ]


async def ensure_candidate_portal_jobs() -> tuple[dict[str, Any], list[Job]]:
    async with local_session() as session:
        for definition in DICTIONARY_DEFINITIONS:
            await ensure_dictionary(session, definition)

        form_template = await ensure_form_template(session)
        role = await ensure_role(session)
        admin = await ensure_admin_user(session, role_id=role.id)
        await session.commit()
        form_fields = list(form_template.fields or [])

    archived_obsolete_job_count = await archive_obsolete_candidate_portal_jobs(admin_user_id=int(admin.id))
    mail_ids = await ensure_assessment_mail_dependencies(admin_user_id=admin.id)
    rejection_mail_ids = await ensure_rejection_mail_dependencies(admin_user_id=admin.id)
    jobs: list[Job] = []
    for definition in PORTAL_JOB_DEFINITIONS:
        rejection_enabled = str(definition["key"]) == "rejected"
        assessment_enabled = bool(definition["assessment_enabled"])
        async with local_session() as session:
            company = await ensure_company(session, name=definition["company_name"])
            project = await ensure_company_project(
                session,
                company_id=company.id,
                name=definition.get("project_name", "Default Project"),
            )
            result = await session.execute(
                select(Job).where(
                    Job.owner_admin_user_id == admin.id,
                    Job.is_deleted.is_(False),
                )
            )
            job = next(
                (
                    candidate_job
                    for candidate_job in result.scalars().all()
                    if get_candidate_portal_demo_case_key(candidate_job) == str(definition["key"])
                ),
                None,
            )
            data = {
                JOB_DATA_FORM_FIELDS_KEY: form_fields,
                JOB_DATA_AUTOMATION_RULES_KEY: definition["automation_rules"],
                CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY: str(definition["key"]),
                JOB_DATA_CONTRACT_EXAMPLE_KEY: definition.get("contract_example")
                or build_contract_example_html(
                    job_title=definition["title"],
                    company_name=company.name,
                    compensation_unit=str(definition["compensation_unit"]),
                ),
            }
            referral_bonus_model = await ensure_referral_bonus_model(session)
            if rejection_enabled:
                data["rejection_mail_config"] = {
                    "enabled": True,
                    "mail_account_id": rejection_mail_ids["mail_account_id"],
                    "mail_template_id": rejection_mail_ids["mail_template_id"],
                    "mail_signature_id": rejection_mail_ids["mail_signature_id"],
                    "mail_account_label": rejection_mail_ids.get("mail_account_label"),
                    "mail_template_name": "流程淘汰通知模板",
                    "mail_signature_name": "流程淘汰签名",
                }
            if job is None:
                job = Job(
                    title=definition["title"],
                    company_id=company.id,
                    project_id=project.id,
                    referral_bonus_model_id=referral_bonus_model.id,
                    country=definition["country"],
                    status=JobStatus.OPEN.value,
                    work_mode=definition["work_mode"],
                    compensation_min=definition["compensation_min"],
                    compensation_max=definition["compensation_max"],
                    compensation_unit=definition["compensation_unit"],
                    description=definition["description"],
                    applicant_count=0,
                    owner_admin_user_id=admin.id,
                    form_template_id=form_template.id,
                    assessment_enabled=assessment_enabled,
                    assessment_mail_account_id=mail_ids["mail_account_id"] if assessment_enabled else None,
                    assessment_mail_template_id=mail_ids["mail_template_id"] if assessment_enabled else None,
                    assessment_mail_signature_id=mail_ids["mail_signature_id"] if assessment_enabled else None,
                    data=data,
                )
                session.add(job)
            else:
                job.title = definition["title"]
                job.company_id = company.id
                job.project_id = project.id
                job.referral_bonus_model_id = referral_bonus_model.id
                job.country = definition["country"]
                job.status = JobStatus.OPEN.value
                job.work_mode = definition["work_mode"]
                job.compensation_min = definition["compensation_min"]
                job.compensation_max = definition["compensation_max"]
                job.compensation_unit = definition["compensation_unit"]
                job.description = definition["description"]
                job.form_template_id = form_template.id
                job.assessment_enabled = assessment_enabled
                job.assessment_mail_account_id = mail_ids["mail_account_id"] if assessment_enabled else None
                job.assessment_mail_template_id = mail_ids["mail_template_id"] if assessment_enabled else None
                job.assessment_mail_signature_id = mail_ids["mail_signature_id"] if assessment_enabled else None
                job.data = data
                job.is_deleted = False
                job.deleted_at = None
            await session.commit()
            await session.refresh(job)
            jobs.append(job)

    return {
        "admin": {
            "username": DEMO_ADMIN_USERNAME,
            "email": DEMO_ADMIN_EMAIL,
            "password": DEMO_ADMIN_PASSWORD,
        },
        "archived_obsolete_job_count": archived_obsolete_job_count,
    }, jobs


async def archive_obsolete_candidate_portal_jobs(*, admin_user_id: int) -> int:
    now = datetime.now(UTC)
    async with local_session() as session:
        result = await session.execute(
            select(Job).where(
                Job.owner_admin_user_id == admin_user_id,
                Job.is_deleted.is_(False),
            )
        )
        obsolete_jobs = [
            job
            for job in result.scalars().all()
            if is_candidate_portal_demo_owned_job(job) and not is_current_candidate_portal_demo_job(job)
        ]
        for job in obsolete_jobs:
            job.is_deleted = True
            job.deleted_at = now
            job.status = JobStatus.CLOSED.value
        await session.commit()
        return len(obsolete_jobs)


async def login_admin(
    client: httpx.AsyncClient,
    *,
    username_or_email: str,
    password: str,
) -> dict[str, Any]:
    response = await client.post(
        "/auth/login",
        json={
            "username_or_email": username_or_email,
            "password": password,
        },
    )
    return ensure_ok(response, "Admin login failed")


async def logout_candidate(client: httpx.AsyncClient) -> None:
    response = await client.post("/logout")
    payload = ensure_ok(response, "Candidate logout failed")
    print_detail(payload["message"])


async def admin_move_stage(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_ids: list[int],
    target_stage: str,
    reason: str,
) -> None:
    response = await client.post(
        f"/jobs/{job_id}/progress/stage",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "progress_ids": progress_ids,
            "target_stage": target_stage,
            "reason": reason,
        },
    )
    ensure_ok(response, f"Move stage to {target_stage} failed")


async def ensure_admin_stage(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    case: dict[str, Any],
    target_stage: str,
    reason: str,
) -> None:
    current_stage = str(case.get("detail", {}).get("current_stage") or "")
    if current_stage == target_stage:
        return
    await admin_move_stage(
        client,
        access_token=access_token,
        job_id=int(case["job"].id),
        progress_ids=[int(case["job_progress_id"])],
        target_stage=target_stage,
        reason=reason,
    )
    next_detail = dict(case.get("detail") or {})
    next_detail["current_stage"] = target_stage
    case["detail"] = next_detail


async def admin_upload_contract_draft(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_id: int,
    file_name: str = "draft-contract.pdf",
) -> None:
    response = await client.post(
        f"/jobs/{job_id}/progress/contract-draft/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files={
            "progress_id": (None, str(progress_id)),
            "file": (
                file_name,
                build_demo_pdf_bytes(
                    candidate_email=file_name,
                    note="Candidate portal contract-pool demo file.",
                ),
                "application/pdf",
            ),
        },
    )
    ensure_ok(response, "Upload contract draft failed")


async def admin_upload_company_sealed_contract(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_id: int,
    file_name: str = "company-sealed-contract.pdf",
) -> None:
    response = await client.post(
        f"/jobs/{job_id}/progress/company-sealed-contract/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files={
            "progress_id": (None, str(progress_id)),
            "file": (
                file_name,
                build_demo_pdf_bytes(
                    candidate_email=file_name,
                    note="Candidate portal company sealed contract demo file.",
                ),
                "application/pdf",
            ),
        },
    )
    ensure_ok(response, "Upload company sealed contract failed")


async def refresh_active_contract_attachment(
    *,
    progress_id: int,
    admin_user_id: int,
    file_name: str = "Active 2026 003 Company Returned Contract.pdf",
) -> int | None:
    async with local_session() as session:
        progress = await session.get(JobProgress, progress_id)
        if progress is None or progress.is_deleted:
            return None

        result = await session.execute(
            select(ContractRecord)
            .where(
                ContractRecord.job_progress_id == progress_id,
                ContractRecord.is_deleted.is_(False),
            )
            .order_by(ContractRecord.is_current.desc(), ContractRecord.version.desc(), ContractRecord.id.desc())
        )
        contract = result.scalars().first()
        if contract is None:
            return None

        asset = await create_asset_from_bytes(
            db=session,
            payload=AssetUploadPayload(
                type="file",
                module="job_progress",
                owner_type="job_progress",
                owner_id=progress_id,
            ),
            original_name=file_name,
            content=build_demo_pdf_bytes(
                candidate_email=file_name,
                note="Candidate portal active contract attachment refresh.",
            ),
            mime_type="application/pdf",
            data={"generated_by": "run_candidate_my_jobs_demo"},
        )
        contract.company_sealed_contract_asset_id = int(asset.id)
        contract.contract_attachment_asset_id = int(asset.id)
        contract.contract_status = "Active"
        contract.updated_by_admin_user_id = admin_user_id
        contract.effective_date = contract.effective_date or datetime.now(UTC).date()
        next_contract_data = dict(contract.data or {})
        next_contract_data["company_sealed_contract_attachment_name"] = asset.original_name
        next_contract_data["company_sealed_contract_uploaded_at"] = datetime.now(UTC).isoformat()
        contract.data = next_contract_data
        await session.commit()
        return int(asset.id)


async def update_progress_demo_state(
    *,
    progress_id: int,
    current_stage: str | None = None,
    process_data_updates: dict[str, Any] | None = None,
) -> None:
    async with local_session() as session:
        progress = await session.get(JobProgress, progress_id)
        if progress is None or progress.is_deleted:
            raise RuntimeError(f"Job progress not found for progress_id={progress_id}")
        if current_stage is not None:
            progress.current_stage = current_stage
            progress.entered_stage_at = datetime.now(UTC)
        if process_data_updates:
            next_data = dict(progress.data or {})
            next_data.update(process_data_updates)
            progress.data = next_data
        await session.commit()


async def prepare_candidate_assessment_submission(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    case: dict[str, Any],
    candidate_email: str,
    file_name: str,
) -> None:
    sent_at = datetime.now(UTC).isoformat()
    await update_progress_demo_state(
        progress_id=int(case["job_progress_id"]),
        current_stage="pending_screening",
        process_data_updates={
            "assessment_invited_at": sent_at,
            "assessment_sent_at": sent_at,
            "assessment_invite_mail_task_id": f"candidate-portal-demo-{case['job_progress_id']}",
        },
    )
    await upload_assessment(
        client,
        access_token=access_token,
        job_id=int(case["job"].id),
        file_name=file_name,
        file_bytes=build_demo_xlsx_bytes(
            candidate_email=candidate_email,
            note=f"Candidate portal demo submission for {case['definition']['key']}.",
        ),
    )
    next_detail = dict(case.get("detail") or {})
    next_detail["current_stage"] = "assessment_review"
    case["detail"] = next_detail


async def admin_update_contract_record(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_ids: list[int],
    agreement_ref_no: str | None = None,
    signing_status: str | None = None,
    contract_review: str | None = None,
    rate: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"progress_ids": progress_ids}
    if agreement_ref_no is not None:
        payload["agreement_ref_no"] = agreement_ref_no
    if signing_status is not None:
        payload["signing_status"] = signing_status
    if contract_review is not None:
        payload["contract_review"] = contract_review
    if rate is not None:
        payload["rate"] = rate
    response = await client.patch(
        f"/jobs/{job_id}/progress/contract-record",
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
    )
    return ensure_ok(response, "Update contract record failed")


async def fetch_existing_application(user_id: int, job_id: int) -> dict[str, int] | None:
    async with local_session() as session:
        result = await session.execute(
            select(CandidateApplication, JobProgress)
            .join(JobProgress, JobProgress.application_id == CandidateApplication.id)
            .where(
                CandidateApplication.user_id == user_id,
                CandidateApplication.job_id == job_id,
                CandidateApplication.is_deleted.is_(False),
                JobProgress.is_deleted.is_(False),
            )
            .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        )
        row = result.first()
        if row is None:
            return None
        application, progress = row
        return {
            "application_id": int(application.id),
            "talent_profile_id": int(progress.talent_profile_id or 0),
            "job_progress_id": int(progress.id),
        }


def mail_task_targets_email(task: MailTask, email: str) -> bool:
    normalized_email = email.strip().lower()
    return any(str(item.get("email") or "").strip().lower() == normalized_email for item in (task.to_recipients or []))


def mail_task_targets_demo_scope(
    task: MailTask,
    *,
    candidate_email: str,
    job_ids: set[int],
    application_ids: set[int],
    progress_ids: set[int],
) -> bool:
    if not mail_task_targets_email(task, candidate_email):
        return False

    task_data = task.data if isinstance(task.data, dict) else {}
    render_context = task_data.get("render_context")
    if not isinstance(render_context, dict):
        return False

    job_context = render_context.get("job")
    if isinstance(job_context, dict):
        try:
            if int(job_context.get("id") or 0) in job_ids:
                return True
        except (TypeError, ValueError):
            pass
        job_title = str(job_context.get("title") or "")
        if job_title.startswith(LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX):
            return True

    progress_context = render_context.get("job_progress")
    if isinstance(progress_context, dict):
        try:
            if int(progress_context.get("id") or 0) in progress_ids:
                return True
        except (TypeError, ValueError):
            pass

    serialized_context = json.dumps(render_context, ensure_ascii=False, default=str)
    return any(f"/my-jobs/{application_id}" in serialized_context for application_id in application_ids)


async def reset_candidate_portal_demo_state(
    *,
    user_id: int,
    candidate_email: str,
    job_ids: list[int],
) -> dict[str, int]:
    now = datetime.now(UTC)
    async with local_session() as session:
        demo_job_result = await session.execute(select(Job))
        demo_job_ids = {
            int(job.id) for job in demo_job_result.scalars().all() if is_candidate_portal_demo_owned_job(job)
        }
        scoped_job_ids = sorted({int(job_id) for job_id in job_ids}.union(demo_job_ids))

        application_result = await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.user_id == user_id,
                CandidateApplication.job_id.in_(scoped_job_ids),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        applications = list(application_result.scalars().all())
        application_ids = [int(application.id) for application in applications]

        progress_result = await session.execute(
            select(JobProgress).where(
                JobProgress.user_id == user_id,
                JobProgress.job_id.in_(scoped_job_ids),
                JobProgress.is_deleted.is_(False),
            )
        )
        progresses = list(progress_result.scalars().all())
        progress_ids = [int(progress.id) for progress in progresses]

        contract_conditions = [
            ContractRecord.user_id == user_id,
            ContractRecord.is_deleted.is_(False),
        ]
        scoped_contract_conditions: list[Any] = []
        if scoped_job_ids:
            scoped_contract_conditions.append(ContractRecord.job_id.in_(scoped_job_ids))
        if progress_ids:
            scoped_contract_conditions.append(ContractRecord.job_progress_id.in_(progress_ids))
        if application_ids:
            scoped_contract_conditions.append(ContractRecord.application_id.in_(application_ids))

        contracts: list[ContractRecord] = []
        if scoped_contract_conditions:
            contract_result = await session.execute(
                select(ContractRecord).where(
                    *contract_conditions,
                    or_(*scoped_contract_conditions),
                )
            )
            contracts = list(contract_result.scalars().all())

        for contract in contracts:
            contract.is_deleted = True
            contract.deleted_at = now
            contract.is_current = False

        for progress in progresses:
            progress.is_deleted = True
            progress.deleted_at = now

        for application in applications:
            application.is_deleted = True
            application.deleted_at = now

        mail_task_result = await session.execute(select(MailTask))
        mail_tasks = [
            task
            for task in mail_task_result.scalars().all()
            if mail_task_targets_demo_scope(
                task,
                candidate_email=candidate_email,
                job_ids=set(scoped_job_ids),
                application_ids=set(application_ids),
                progress_ids=set(progress_ids),
            )
        ]
        for task in mail_tasks:
            await session.delete(task)

        await session.commit()
        return {
            "applications": len(applications),
            "progresses": len(progresses),
            "contracts": len(contracts),
            "mail_tasks": len(mail_tasks),
        }


async def list_mail_tasks() -> list[MailTask]:
    async with local_session() as session:
        result = await session.execute(select(MailTask).order_by(MailTask.id.asc()))
        return list(result.scalars().all())


def _subjects_for_recipient(tasks: list[MailTask], email: str) -> set[str]:
    normalized_email = email.strip().lower()
    subjects: set[str] = set()
    for task in tasks:
        recipients = task.to_recipients or []
        if any(str(item.get("email") or "").strip().lower() == normalized_email for item in recipients):
            subjects.add(str(task.subject))
    return subjects


async def fetch_my_applications(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> list[dict[str, Any]]:
    payload = await fetch_my_applications_page(client, access_token=access_token, page_size=100)
    return list(payload.get("items", []))


async def fetch_my_applications_page(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    page: int = 1,
    page_size: int = 10,
    current_stage: str | None = None,
    needs_action_only: bool = False,
    keyword: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
    }
    if current_stage:
        params["current_stage"] = current_stage
    if needs_action_only:
        params["needs_action_only"] = True
    if keyword:
        params["keyword"] = keyword

    response = await client.get(
        "/me/applications",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    payload = ensure_ok(response, "List Applications failed")
    return payload


async def fetch_my_application_detail(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    application_id: int,
) -> dict[str, Any]:
    response = await client.get(
        f"/me/applications/{application_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return ensure_ok(response, "Load My Job detail failed")


async def fetch_my_contracts_page(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    page: int = 1,
    page_size: int = 10,
    keyword: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
    }
    if keyword:
        params["keyword"] = keyword

    response = await client.get(
        "/me/contracts",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    return ensure_ok(response, "List My Contracts failed")


async def candidate_upload_signed_contract_response(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    file_name: str = "candidate-signed-contract.docx",
) -> httpx.Response:
    media_type = (
        "application/msword"
        if file_name.lower().endswith(".doc")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return await client.post(
        f"/jobs/{job_id}/signed-contract/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files={
            "file": (
                file_name,
                build_demo_docx_bytes(
                    candidate_email=file_name,
                    note="Candidate portal signed contract demo file.",
                ),
                media_type,
            )
        },
    )


async def download_asset(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    asset_path: str,
) -> httpx.Response:
    return await client.get(
        asset_path,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def assert_candidate_demo_item_matches_definition(item: dict[str, Any], definition: dict[str, Any]) -> None:  # noqa: C901
    key = str(definition["key"])
    process_data = item.get("process_data") or {}
    contract_data = item.get("contract_record_data") or {}
    current_stage = str(item.get("current_stage") or "")

    if current_stage != str(definition["target_stage"]):
        raise RuntimeError(
            f"Job {definition['title']} expected stage={definition['target_stage']}, got {current_stage}"
        )

    expected_presentation = EXPECTED_CANDIDATE_VIEW_BY_KEY[key]
    actual_presentation = {
        field: item.get(field)
        for field in (
            "candidate_status",
            "candidate_stage",
            "candidate_action",
            "candidate_action_required",
        )
    }
    if actual_presentation != expected_presentation:
        raise RuntimeError(
            f"Job {definition['title']} presentation mismatch: "
            f"expected={expected_presentation}, got={actual_presentation}"
        )

    if key == "application_review":
        if process_data.get("assessment_sent_at"):
            raise RuntimeError("Application Review demo should not have assessment_sent_at.")
    elif key == "assessment_action_required":
        if not process_data.get("assessment_sent_at"):
            raise RuntimeError("Assessment action-required demo is missing assessment_sent_at.")
    elif key == "assessment_under_review":
        if not process_data.get("assessment_submitted_at"):
            raise RuntimeError("Assessment under-review demo is missing assessment_submitted_at.")
        if not process_data.get("assessment_attachment"):
            raise RuntimeError("Assessment under-review demo is missing its uploaded file.")
    elif key == "assessment_revision_required":
        if process_data.get("assessment_result") != "需重新提交":
            raise RuntimeError("Assessment revision demo should have assessment_result=需重新提交.")
        if not process_data.get("assessment_attachment"):
            raise RuntimeError("Assessment revision demo is missing its uploaded file.")
    elif key == "rate_confirmation_waiting":
        if process_data.get("assessment_result") != "通过":
            raise RuntimeError("Rate waiting demo should have assessment_result=通过.")
        if not process_data.get("assessment_attachment"):
            raise RuntimeError("Rate waiting demo is missing its uploaded file.")
        if process_data.get("onboarding_status") == "已发砍价":
            raise RuntimeError("Rate waiting demo should not have 已发砍价 yet.")
        if contract_data.get("draft_contract_attachment"):
            raise RuntimeError("Rate waiting demo should not have a draft contract yet.")
    elif key == "rate_confirmation_action_required":
        if process_data.get("onboarding_status") != "已发砍价":
            raise RuntimeError("Rate action-required demo should have onboarding_status=已发砍价.")
        if contract_data.get("draft_contract_attachment"):
            raise RuntimeError("Rate action-required demo should not have a draft contract yet.")
    elif key == "signed_contract_action_required":
        if not contract_data.get("draft_contract_attachment"):
            raise RuntimeError("Signed contract action-required demo is missing draft_contract_attachment.")
        if contract_data.get("candidate_signed_contract_attachment"):
            raise RuntimeError("Signed contract action-required demo should not have candidate signed attachment.")
    elif key == "signed_contract_under_review":
        if not contract_data.get("candidate_signed_contract_attachment"):
            raise RuntimeError("Signed contract under-review demo is missing candidate signed attachment.")
        if contract_data.get("contract_review") == "待修改":
            raise RuntimeError("Signed contract under-review demo should not be in 待修改.")
    elif key == "signed_contract_revision_required":
        if not contract_data.get("candidate_signed_contract_attachment"):
            raise RuntimeError("Signed contract revision demo is missing candidate signed attachment.")
        if contract_data.get("contract_review") != "待修改":
            raise RuntimeError("Signed contract revision demo should have contract_review=待修改.")
    elif key == "onboarding_preparation":
        if not contract_data.get("company_sealed_contract_attachment"):
            raise RuntimeError("Onboarding preparation demo is missing company sealed contract.")
        if process_data.get("onboarding_status") != "成功签约":
            raise RuntimeError("Onboarding preparation demo should still be waiting for the guide.")
    elif key == "task_group_action_required":
        if process_data.get("onboarding_status") != "已发大礼包":
            raise RuntimeError("Task group demo should have onboarding_status=已发大礼包.")
        if process_data.get("onboarding_date"):
            raise RuntimeError("Task group demo should not have onboarding_date yet.")
    elif key == "successfully_onboarded":
        if not process_data.get("onboarding_date"):
            raise RuntimeError("Successfully onboarded demo is missing onboarding_date.")
    elif key == "rejected":
        if current_stage != "rejected":
            raise RuntimeError("Rejected demo should be in rejected stage.")
    elif key == "rejected_late_stage":
        if process_data.get("rejected_from_stage") != "contract_pool":
            raise RuntimeError("Late-stage rejection demo should preserve rejected_from_stage=contract_pool.")
    elif key == "engagement_ended":
        if current_stage != "replaced" or not process_data.get("onboarding_date"):
            raise RuntimeError("Engagement-ended demo should preserve its completed onboarding milestone.")


async def main() -> None:  # noqa: C901
    args = parse_args()

    auto_apply_count = len([definition for definition in PORTAL_JOB_DEFINITIONS if should_auto_apply(definition)])
    print_step(f"Step 1/6: seed admin, one fresh job, and {auto_apply_count} candidate demo jobs")
    seed_payload, jobs = await ensure_candidate_portal_jobs()
    print_detail(f"archived obsolete demo jobs: {seed_payload['archived_obsolete_job_count']}")
    for job in jobs:
        print_detail(f"job ready: {job.id} {job.title}")

    async with (
        httpx.AsyncClient(
            transport=ASGITransport(app=web_app),
            base_url=WEB_BASE_URL,
            timeout=30.0,
        ) as web_client,
        httpx.AsyncClient(
            transport=ASGITransport(app=admin_app),
            base_url=ADMIN_BASE_URL,
            timeout=30.0,
        ) as admin_client,
    ):
        print_step("Step 2/6: login, logout, and login again with the candidate account")
        await register_or_reuse_candidate(
            web_client,
            name=args.candidate_name,
            email=args.candidate_email,
            password=args.candidate_password,
        )
        first_access_token = await login_candidate(
            web_client,
            email=args.candidate_email,
            password=args.candidate_password,
        )
        current_user = await fetch_current_user(web_client, access_token=first_access_token)
        await logout_candidate(web_client)
        access_token = await login_candidate(
            web_client,
            email=args.candidate_email,
            password=args.candidate_password,
        )
        current_user = await fetch_current_user(web_client, access_token=access_token)
        print_detail(f"candidate user_id={current_user['id']} email={current_user['email']}")

        resume_asset = await ensure_resume_asset(user_id=int(current_user["id"]), email=args.candidate_email)
        admin_login_payload = await login_admin(
            admin_client,
            username_or_email=seed_payload["admin"]["username"],
            password=seed_payload["admin"]["password"],
        )
        admin_access_token = admin_login_payload["access_token"]

        print_step("Step 3/6: apply to each demo job once, and leave one fresh role for manual end-to-end testing")
        reset_summary = await reset_candidate_portal_demo_state(
            user_id=int(current_user["id"]),
            candidate_email=args.candidate_email,
            job_ids=[int(job.id) for job in jobs],
        )
        print_detail(
            "reset prior portal demo state: "
            f"applications={reset_summary['applications']} "
            f"progresses={reset_summary['progresses']} "
            f"contracts={reset_summary['contracts']} "
            f"mail_tasks={reset_summary['mail_tasks']}"
        )
        cases_by_key: dict[str, dict[str, Any]] = {}
        for definition, job in zip(PORTAL_JOB_DEFINITIONS, jobs, strict=True):
            if not should_auto_apply(definition):
                print_detail(f"left unapplied for manual testing: {job.title} (job_id={job.id})")
                continue
            items = build_application_items(
                scenario_key=str(definition["application_scenario"]),
                candidate_name=args.candidate_name,
                candidate_email=args.candidate_email,
                resume_asset_id=resume_asset.id,
            )
            try:
                application_payload = await submit_application(
                    web_client,
                    access_token=access_token,
                    job_id=job.id,
                    items=items,
                )
                application_id = int(application_payload["application_id"])
                print_detail(f"applied: {job.title} -> application_id={application_id}")
            except RuntimeError as exc:
                if "already applied to this role" not in str(exc):
                    raise
                existing = await fetch_existing_application(int(current_user["id"]), int(job.id))
                if existing is None:
                    raise
                application_id = int(existing["application_id"])
                print_detail(f"reused existing application: {job.title} -> application_id={application_id}")
            cases_by_key[str(definition["key"])] = {
                "definition": definition,
                "job": job,
                "application_id": application_id,
            }

        duplicate_job = next(
            job for definition, job in zip(PORTAL_JOB_DEFINITIONS, jobs, strict=True) if should_auto_apply(definition)
        )
        duplicate_items = build_application_items(
            scenario_key=str(
                next(
                    definition["application_scenario"]
                    for definition in PORTAL_JOB_DEFINITIONS
                    if should_auto_apply(definition)
                )
            ),
            candidate_name=args.candidate_name,
            candidate_email=args.candidate_email,
            resume_asset_id=resume_asset.id,
        )
        duplicate_response = await web_client.post(
            f"/jobs/{duplicate_job.id}/apply",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"items": duplicate_items},
        )
        if duplicate_response.status_code != 400:
            raise RuntimeError(
                "Duplicate application should fail with 400, "
                f"got {duplicate_response.status_code} {duplicate_response.text}"
            )
        duplicate_detail = duplicate_response.json().get("detail")
        print_detail(f"duplicate apply blocked for job_id={duplicate_job.id}: {duplicate_detail}")

        if should_verify_auto_assessment_mail_task():
            auto_subjects = _subjects_for_recipient(await list_mail_tasks(), args.candidate_email)
            if "请完成 {{job_title}} 测试题" not in auto_subjects:
                raise RuntimeError("Missing auto-created assessment mail task in candidate-portal demo flow.")
            print_detail("auto mail task created for assessment-review branch")

        print_step("Step 4/6: shape the applications into C-side status demo states")
        list_payload = await fetch_my_applications(web_client, access_token=access_token)
        by_application_id = {int(item["application_id"]): item for item in list_payload}
        for case in cases_by_key.values():
            item = by_application_id[case["application_id"]]
            case["detail"] = item
            case["job_progress_id"] = int(item["job_progress_id"])

        assessment_action_case = cases_by_key["assessment_action_required"]
        assessment_action_sent_at = datetime.now(UTC).isoformat()
        await update_progress_demo_state(
            progress_id=int(assessment_action_case["job_progress_id"]),
            current_stage="pending_screening",
            process_data_updates={
                "assessment_invited_at": assessment_action_sent_at,
                "assessment_sent_at": assessment_action_sent_at,
                "assessment_invite_mail_task_id": "candidate-portal-demo-assessment",
            },
        )
        assessment_action_case["detail"]["current_stage"] = "pending_screening"
        print_detail("assessment action-required job now waits for candidate upload")

        assessment_under_review_case = cases_by_key["assessment_under_review"]
        await prepare_candidate_assessment_submission(
            web_client,
            access_token=access_token,
            case=assessment_under_review_case,
            candidate_email=args.candidate_email,
            file_name="assessment-under-review.xlsx",
        )
        print_detail("assessment under-review job now has a submitted assessment")

        assessment_revision_case = cases_by_key["assessment_revision_required"]
        await prepare_candidate_assessment_submission(
            web_client,
            access_token=access_token,
            case=assessment_revision_case,
            candidate_email=args.candidate_email,
            file_name="assessment-revision-required.xlsx",
        )
        await update_progress_demo_state(
            progress_id=int(assessment_revision_case["job_progress_id"]),
            current_stage="assessment_review",
            process_data_updates={
                "assessment_result": "需重新提交",
                "assessment_review_comment": "Please revise the highlighted answers and upload a new file.",
            },
        )
        print_detail("assessment revision job now has a submitted file returned for revision")

        rate_waiting_case = cases_by_key["rate_confirmation_waiting"]
        await prepare_candidate_assessment_submission(
            web_client,
            access_token=access_token,
            case=rate_waiting_case,
            candidate_email=args.candidate_email,
            file_name=str(rate_waiting_case["definition"]["assessment_submission_file_name"]),
        )
        rate_waiting_at = datetime.now(UTC).isoformat()
        await update_progress_demo_state(
            progress_id=int(rate_waiting_case["job_progress_id"]),
            current_stage="screening_passed",
            process_data_updates={
                "assessment_invited_at": rate_waiting_at,
                "assessment_sent_at": rate_waiting_at,
                "assessment_submitted_at": rate_waiting_at,
                "assessment_result": "通过",
                "assessment_review_comment": "Demo assessment passed; waiting for rate confirmation email.",
            },
        )
        rate_waiting_case["detail"]["current_stage"] = "screening_passed"
        print_detail("rate-confirmation waiting job now sits after assessment pass without rate email")

        rate_action_case = cases_by_key["rate_confirmation_action_required"]
        await update_progress_demo_state(
            progress_id=int(rate_action_case["job_progress_id"]),
            current_stage="screening_passed",
            process_data_updates={
                "onboarding_status": "已发砍价",
                "salary_confirmed_at": datetime.now(UTC).date().isoformat(),
            },
        )
        rate_action_case["detail"]["current_stage"] = "screening_passed"
        print_detail("rate-confirmation action-required job now points the candidate to email instructions")

        signed_action_case = cases_by_key["signed_contract_action_required"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=signed_action_case,
            target_stage="screening_passed",
            reason="Prepare candidate portal signed-contract action-required data.",
        )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_action_case["job"].id),
            progress_ids=[int(signed_action_case["job_progress_id"])],
            agreement_ref_no="SIGN-ACTION-2026-001",
            signing_status="已通知人选签合同",
            rate="4.20",
        )
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_action_case["job"].id),
            progress_id=int(signed_action_case["job_progress_id"]),
            file_name="Signed Action 2026 001 Draft Contract.pdf",
        )
        print_detail("signed-contract action-required job now has a draft contract")

        signed_review_case = cases_by_key["signed_contract_under_review"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=signed_review_case,
            target_stage="screening_passed",
            reason="Prepare candidate portal signed-contract under-review data.",
        )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_review_case["job"].id),
            progress_ids=[int(signed_review_case["job_progress_id"])],
            agreement_ref_no="SIGN-REVIEW-2026-002",
            signing_status="已通知人选签合同",
            rate="6.80",
        )
        failed_signed_upload = await candidate_upload_signed_contract_response(
            web_client,
            access_token=access_token,
            job_id=int(signed_review_case["job"].id),
            file_name="candidate-signed-before-draft.docx",
        )
        if failed_signed_upload.status_code != 400:
            raise RuntimeError(
                "Signed contract upload should fail before the draft contract exists, "
                f"got {failed_signed_upload.status_code} {failed_signed_upload.text}"
            )
        print_detail(f"signed contract upload blocked before draft: {failed_signed_upload.json().get('detail')}")
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_review_case["job"].id),
            progress_id=int(signed_review_case["job_progress_id"]),
            file_name="Signed Review 2026 002 Draft Contract.pdf",
        )
        successful_signed_upload = await candidate_upload_signed_contract_response(
            web_client,
            access_token=access_token,
            job_id=int(signed_review_case["job"].id),
            file_name="candidate-signed-contract-under-review.docx",
        )
        if successful_signed_upload.status_code not in {200, 201}:
            raise RuntimeError(
                "Signed contract upload should succeed after the draft exists, "
                f"got {successful_signed_upload.status_code} {successful_signed_upload.text}"
            )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_review_case["job"].id),
            progress_ids=[int(signed_review_case["job_progress_id"])],
            contract_review="待审核",
        )
        signed_review_case["detail"]["current_stage"] = "contract_pool"
        print_detail("signed-contract under-review job now has a submitted signed contract")

        signed_revision_case = cases_by_key["signed_contract_revision_required"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=signed_revision_case,
            target_stage="screening_passed",
            reason="Prepare candidate portal signed-contract revision data.",
        )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_revision_case["job"].id),
            progress_ids=[int(signed_revision_case["job_progress_id"])],
            agreement_ref_no="SIGN-REVISION-2026-003",
            signing_status="已通知人选签合同",
            rate="7.20",
        )
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_revision_case["job"].id),
            progress_id=int(signed_revision_case["job_progress_id"]),
            file_name="Signed Revision 2026 003 Draft Contract.pdf",
        )
        revision_signed_upload = await candidate_upload_signed_contract_response(
            web_client,
            access_token=access_token,
            job_id=int(signed_revision_case["job"].id),
            file_name="candidate-signed-contract-revision.docx",
        )
        if revision_signed_upload.status_code not in {200, 201}:
            raise RuntimeError(
                "Revision signed contract upload should succeed, "
                f"got {revision_signed_upload.status_code} {revision_signed_upload.text}"
            )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(signed_revision_case["job"].id),
            progress_ids=[int(signed_revision_case["job_progress_id"])],
            contract_review="待修改",
        )
        signed_revision_case["detail"]["current_stage"] = "contract_pool"
        print_detail("signed-contract revision job now has a submitted contract returned for revision")

        for case_key, ref_no, draft_file, signed_file, sealed_file, process_updates in [
            (
                "onboarding_preparation",
                "ONBOARDING-PREP-2026-004",
                "Onboarding Preparation 2026 004 Draft Contract.pdf",
                "candidate-signed-contract-onboarding-preparation.docx",
                "Onboarding Preparation 2026 004 Company Returned Contract.pdf",
                {
                    "onboarding_status": "成功签约",
                },
            ),
            (
                "task_group_action_required",
                "TASK-GROUP-2026-005",
                "Task Group 2026 005 Draft Contract.pdf",
                "candidate-signed-contract-task-group.docx",
                "Task Group 2026 005 Company Returned Contract.pdf",
                {
                    "onboarding_status": "已发大礼包",
                    "gift_package_sent_at": datetime.now(UTC).date().isoformat(),
                },
            ),
            (
                "successfully_onboarded",
                "ONBOARDED-2026-006",
                "Onboarded 2026 006 Draft Contract.pdf",
                "candidate-signed-contract-onboarded.docx",
                "Onboarded 2026 006 Company Returned Contract.pdf",
                {
                    "onboarding_date": datetime.now(UTC).date().isoformat(),
                },
            ),
            (
                "engagement_ended",
                "ENGAGEMENT-END-2026-007",
                "Engagement End 2026 007 Draft Contract.pdf",
                "candidate-signed-contract-engagement-ended.docx",
                "Engagement End 2026 007 Company Returned Contract.pdf",
                {
                    "onboarding_status": "已发大礼包",
                    "onboarding_date": datetime.now(UTC).date().isoformat(),
                },
            ),
        ]:
            active_case = cases_by_key[case_key]
            await ensure_admin_stage(
                admin_client,
                access_token=admin_access_token,
                case=active_case,
                target_stage="screening_passed",
                reason=f"Prepare candidate portal {case_key} data.",
            )
            await admin_update_contract_record(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_ids=[int(active_case["job_progress_id"])],
                agreement_ref_no=ref_no,
                signing_status="已通知人选签合同",
                rate="3500",
            )
            await admin_upload_contract_draft(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_id=int(active_case["job_progress_id"]),
                file_name=draft_file,
            )
            active_signed_upload = await candidate_upload_signed_contract_response(
                web_client,
                access_token=access_token,
                job_id=int(active_case["job"].id),
                file_name=signed_file,
            )
            if active_signed_upload.status_code not in {200, 201}:
                raise RuntimeError(
                    f"{case_key} signed contract upload should succeed, "
                    f"got {active_signed_upload.status_code} {active_signed_upload.text}"
                )
            await admin_update_contract_record(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_ids=[int(active_case["job_progress_id"])],
                contract_review="审核通过",
            )
            await admin_upload_company_sealed_contract(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_id=int(active_case["job_progress_id"]),
                file_name=sealed_file,
            )
            await update_progress_demo_state(
                progress_id=int(active_case["job_progress_id"]),
                current_stage="active",
                process_data_updates=process_updates,
            )
            active_case["detail"]["current_stage"] = "active"
            print_detail(f"{case_key} job now has a completed contract path")

        engagement_ended_case = cases_by_key["engagement_ended"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=engagement_ended_case,
            target_stage="replaced",
            reason="candidate_portal_demo_engagement_ended",
        )
        print_detail("engagement-ended job moved from active to replaced")

        rejected_late_case = cases_by_key["rejected_late_stage"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=rejected_late_case,
            target_stage="screening_passed",
            reason="Prepare candidate portal late-stage rejection data.",
        )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(rejected_late_case["job"].id),
            progress_ids=[int(rejected_late_case["job_progress_id"])],
            agreement_ref_no="REJECT-LATE-2026-008",
            signing_status="已通知人选签合同",
            rate="8.10",
        )
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(rejected_late_case["job"].id),
            progress_id=int(rejected_late_case["job_progress_id"]),
            file_name="Rejected Late 2026 008 Draft Contract.pdf",
        )
        late_signed_upload = await candidate_upload_signed_contract_response(
            web_client,
            access_token=access_token,
            job_id=int(rejected_late_case["job"].id),
            file_name="candidate-signed-contract-before-rejection.docx",
        )
        if late_signed_upload.status_code not in {200, 201}:
            raise RuntimeError(
                "Late-stage signed contract upload should succeed, "
                f"got {late_signed_upload.status_code} {late_signed_upload.text}"
            )
        rejected_late_case["detail"]["current_stage"] = "contract_pool"
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=rejected_late_case,
            target_stage="rejected",
            reason="candidate_portal_demo_rejected_late_stage",
        )
        print_detail("late-stage rejection job moved from signed contract to rejected")

        rejected_case = cases_by_key["rejected"]
        await ensure_admin_stage(
            admin_client,
            access_token=admin_access_token,
            case=rejected_case,
            target_stage="rejected",
            reason="candidate_portal_demo_rejected",
        )
        auto_subjects = _subjects_for_recipient(await list_mail_tasks(), args.candidate_email)
        if "关于 {{job_title}} 的申请结果通知" not in auto_subjects:
            raise RuntimeError("Missing auto-created rejection mail task in candidate-portal demo flow.")
        print_detail("rejected job moved to rejected and rejection mail task created")

        print_step("Step 5/6: verify Applications list and candidate-facing detail APIs")
        seeded_application_job_ids = {int(case["job"].id) for case in cases_by_key.values()}
        refreshed_payload = await fetch_my_applications_page(
            web_client,
            access_token=access_token,
            page_size=100,
        )
        refreshed_items = [
            item
            for item in refreshed_payload.get("items", [])
            if int(item.get("job_id") or 0) in seeded_application_job_ids
        ]
        refreshed_by_title = {str(item["job_title"]): item for item in refreshed_items}
        expected_titles = {
            str(definition["title"]) for definition in PORTAL_JOB_DEFINITIONS if should_auto_apply(definition)
        }
        missing_titles = expected_titles.difference(refreshed_by_title.keys())
        if missing_titles:
            raise RuntimeError(f"Applications missing titles: {sorted(missing_titles)}")
        expected_cases = build_expected_candidate_portal_cases()
        expected_summary = build_expected_candidate_summary(expected_cases)
        actual_demo_summary = build_candidate_summary_from_items(refreshed_items)
        if actual_demo_summary != expected_summary:
            raise RuntimeError(f"Applications summary mismatch: expected={expected_summary}, got={actual_demo_summary}")
        for definition in PORTAL_JOB_DEFINITIONS:
            if not should_auto_apply(definition):
                continue
            item = refreshed_by_title[str(definition["title"])]
            assert_candidate_demo_item_matches_definition(item, definition)
            print_detail(
                f"{definition['title']} -> {item['current_stage']} / "
                f"{item['candidate_status']} / {item['candidate_stage']} / {item['candidate_action']}"
            )

        signed_action_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["signed_contract_action_required"]["application_id"]),
        )
        signed_review_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["signed_contract_under_review"]["application_id"]),
        )
        task_group_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["task_group_action_required"]["application_id"]),
        )
        onboarded_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["successfully_onboarded"]["application_id"]),
        )
        assessment_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["assessment_under_review"]["application_id"]),
        )
        has_assessment_submission = bool((assessment_detail.get("process_data") or {}).get("assessment_submitted_at"))
        print_detail(f"assessment detail submitted_at={has_assessment_submission}")
        print_detail(
            "contract detail draft="
            f"{bool((signed_action_detail.get('contract_record_data') or {}).get('draft_contract_attachment'))}"
        )
        if not signed_action_detail.get("contract_example_html"):
            raise RuntimeError("Signed-contract action detail is missing the contract example content.")
        if not (signed_action_detail.get("contract_record_data") or {}).get("draft_contract_attachment"):
            raise RuntimeError("Signed-contract action detail is missing its draft contract.")
        if not (signed_review_detail.get("contract_record_data") or {}).get("candidate_signed_contract_attachment"):
            raise RuntimeError("Signed-contract under-review detail is missing the candidate signed contract.")
        if not (task_group_detail.get("contract_record_data") or {}).get("company_sealed_contract_attachment"):
            raise RuntimeError("Task group detail is missing the company returned contract attachment.")
        if (task_group_detail.get("contract_record_data") or {}).get("contract_status") != "Active":
            raise RuntimeError("Task group detail should already be marked Active.")
        if not (onboarded_detail.get("process_data") or {}).get("onboarding_date"):
            raise RuntimeError("Successfully onboarded detail is missing onboarding_date.")
        draft_download_url = (
            (signed_action_detail.get("contract_record_data") or {})
            .get("draft_contract_attachment", {})
            .get("download_url")
        )
        if not draft_download_url:
            raise RuntimeError("Contract detail is missing the draft contract download URL.")
        download_response = await download_asset(
            web_client,
            access_token=access_token,
            asset_path=str(draft_download_url).removeprefix("/api/v1"),
        )
        if download_response.status_code != 200:
            raise RuntimeError(
                f"Draft contract download should succeed, got {download_response.status_code} {download_response.text}"
            )
        print_detail("contract draft download returned 200 for the candidate user")

        my_contracts_payload = await fetch_my_contracts_page(
            web_client,
            access_token=access_token,
        )
        my_contract_titles = {str(item["job_title"]): item for item in my_contracts_payload.get("items", [])}
        expected_contract_titles = {
            str(cases_by_key["onboarding_preparation"]["job"].title),
            str(cases_by_key["task_group_action_required"]["job"].title),
            str(cases_by_key["successfully_onboarded"]["job"].title),
        }
        missing_contract_titles = expected_contract_titles.difference(my_contract_titles.keys())
        if missing_contract_titles:
            raise RuntimeError(f"My Contracts missing titles: {sorted(missing_contract_titles)}")
        inactive_contract_titles = {
            str(cases_by_key["signed_contract_action_required"]["job"].title),
            str(cases_by_key["signed_contract_revision_required"]["job"].title),
            str(cases_by_key["signed_contract_under_review"]["job"].title),
            str(cases_by_key["engagement_ended"]["job"].title),
        }.intersection(my_contract_titles.keys())
        if inactive_contract_titles:
            raise RuntimeError(
                f"My Contracts should only show active contracts, got: {sorted(inactive_contract_titles)}"
            )
        print_detail(f"my contracts endpoint works: items={my_contracts_payload['total']}")

        paged_payload = await fetch_my_applications_page(
            web_client,
            access_token=access_token,
            page=1,
            page_size=2,
        )
        if int(paged_payload.get("page", 0)) != 1 or int(paged_payload.get("page_size", 0)) != 2:
            raise RuntimeError(f"Unexpected pagination payload: {paged_payload}")
        if len(paged_payload.get("items", [])) > 2:
            raise RuntimeError("Applications page_size=2 returned more than two items.")
        if paged_payload.get("summary") != refreshed_payload.get("summary"):
            raise RuntimeError("Paged Applications summary should still describe the full candidate result.")
        print_detail(
            f"pagination works: page={paged_payload['page']} page_size={paged_payload['page_size']} "
            f"items={len(paged_payload['items'])} total={paged_payload['total']}"
        )

        contract_filter_payload = await fetch_my_applications_page(
            web_client,
            access_token=access_token,
            current_stage="contract_pool",
        )
        if not contract_filter_payload.get("items"):
            raise RuntimeError("Contract-pool filter returned no items.")
        if any(item.get("current_stage") != "contract_pool" for item in contract_filter_payload["items"]):
            raise RuntimeError("Contract-pool filter returned items from another stage.")
        print_detail(f"stage filter works: contract_pool items={len(contract_filter_payload['items'])}")

        needs_action_payload = await fetch_my_applications_page(
            web_client,
            access_token=access_token,
            needs_action_only=True,
        )
        needs_action_items = [
            item
            for item in needs_action_payload.get("items", [])
            if int(item.get("job_id") or 0) in seeded_application_job_ids
        ]
        if not needs_action_items:
            raise RuntimeError("Needs-action filter returned no items.")
        if any(not item.get("candidate_action_required") for item in needs_action_items):
            raise RuntimeError("Needs-action filter returned a passive candidate presentation.")
        needs_action_titles = {str(item["job_title"]) for item in needs_action_items}
        expected_needs_action_titles = {
            str(cases_by_key[key]["job"].title) for key in get_expected_action_required_case_keys(expected_cases)
        }
        if expected_needs_action_titles != needs_action_titles:
            raise RuntimeError(
                "Needs-action filter mismatch: "
                f"expected={sorted(expected_needs_action_titles)}, got={sorted(needs_action_titles)}"
            )
        print_detail(f"needs-action filter works: items={len(needs_action_items)}")

        print_step("Step 6/6: ready-to-test summary")
        print(f"candidate email: {args.candidate_email}")
        print(f"candidate password: {args.candidate_password}")
        fresh_job = next(
            job
            for definition, job in zip(PORTAL_JOB_DEFINITIONS, jobs, strict=True)
            if not should_auto_apply(definition)
        )
        print(f"fresh job title: {fresh_job.title}")
        print(f"fresh job id: {fresh_job.id}")
        print("applications summary:")
        for definition in PORTAL_JOB_DEFINITIONS:
            if not should_auto_apply(definition):
                continue
            item = refreshed_by_title[str(definition["title"])]
            print(
                "  - "
                f"{definition['title']}: stage={item['current_stage']} "
                f"status={item['candidate_status']} "
                f"step={item['candidate_stage']} "
                f"action={item['candidate_action']}"
            )
        print("my contracts summary:")
        for title in sorted(expected_contract_titles):
            contract_item = my_contract_titles[title]
            print(
                "  - "
                f"{title}: stage={contract_item['current_stage']} "
                f"status={(contract_item.get('contract_record_data') or {}).get('contract_status')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
