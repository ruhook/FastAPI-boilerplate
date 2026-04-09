import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from ..app.core.db.database import async_engine, local_session
from ..app.modules.admin.mail_account.const import (
    MAIL_ACCOUNT_PROVIDER_PRESETS,
    MailAccountProvider,
    MailAccountStatus,
)
from ..app.modules.admin.mail_account.model import MailAccount
from ..app.modules.admin.mail_signature.model import MailSignature
from ..app.modules.admin.mail_template.model import MailTemplate
from ..app.modules.admin.mail_template_category.model import MailTemplateCategory
from ..app.modules.candidate_field.const import CandidateFieldKey
from ..app.modules.job.const import JOB_DATA_AUTOMATION_RULES_KEY, JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..app.modules.job.model import Job
from ..app.modules.job_progress.service import get_job_progress_by_application_id, serialize_job_progress
from ..app.main_web import app as web_app
from .run_client_apply_demo import (
    ensure_resume_asset,
    fetch_current_user,
    login_candidate,
    register_or_reuse_candidate,
    submit_application,
)
from .seed_apply_demo_flow import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_ADMIN_USERNAME,
    ensure_admin_user,
    ensure_dictionary,
    ensure_form_template,
    ensure_role,
)
from .seed_candidate_base_form_template import DICTIONARY_DEFINITIONS


DEFAULT_BASE_URL = "http://testserver/api/v1"
DEFAULT_CANDIDATE_NAME = "Progress Demo Candidate"
DEFAULT_CANDIDATE_EMAIL = "progress.demo.candidate@example.com"
DEFAULT_CANDIDATE_PASSWORD = "Candidate123!"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed retained demo data for job progress verification.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Web API base URL.")
    parser.add_argument("--candidate-name", default=DEFAULT_CANDIDATE_NAME, help="Candidate display name.")
    parser.add_argument("--candidate-email", default=DEFAULT_CANDIDATE_EMAIL, help="Candidate email.")
    parser.add_argument("--candidate-password", default=DEFAULT_CANDIDATE_PASSWORD, help="Candidate password.")
    return parser.parse_args()


def _build_rule(
    *,
    field_key: CandidateFieldKey,
    operator: str,
    value: str | int | float | list[str],
    field_type: str = "text",
    second_value: str | int | float | None = None,
) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "fieldKey": field_key.value,
        "fieldLabel": field_key.value,
        "fieldType": field_type,
        "operator": operator,
        "value": value,
    }
    if second_value is not None:
        rule["secondValue"] = second_value
    return rule


def _build_rule_group(*rules: dict[str, Any], combinator: str = "and") -> dict[str, Any]:
    return {"combinator": combinator, "rules": list(rules)}


def build_application_items(
    *,
    scenario_key: str,
    candidate_name: str,
    candidate_email: str,
    resume_asset_id: int,
) -> list[dict[str, Any]]:
    base = {
        CandidateFieldKey.FULL_NAME.value: candidate_name,
        CandidateFieldKey.EMAIL.value: candidate_email,
        CandidateFieldKey.NATIONALITY.value: "Brazilian",
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value: "Brazil",
        CandidateFieldKey.NATIVE_LANGUAGES.value: "Portuguese",
        CandidateFieldKey.ADDITIONAL_LANGUAGES.value: "English",
        CandidateFieldKey.AGE_RANGE.value: ("26_30", "26_30"),
        CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value: ("4_8_hours", "4_8_hours"),
        CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value: ("yes", "yes"),
        CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value: ("6_10", "6_10"),
        CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE.value: ("1_2_years", "1_2_years"),
        CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP.value: ("no_sponsorship_required", "no_sponsorship_required"),
        CandidateFieldKey.RESUME_ATTACHMENT.value: ("progress-demo-resume.pdf", "progress-demo-resume.pdf"),
        CandidateFieldKey.JOB_SOURCE.value: ("linkedin_job_post", "linkedin_job_post"),
    }

    scenario_overrides: dict[str, Any]
    if scenario_key == "assessment_auto_pass":
        scenario_overrides = {
            CandidateFieldKey.WHATSAPP.value: "+55 11 91000 0001",
            CandidateFieldKey.EDUCATION_STATUS.value: ("master_completed", "master_completed"),
            CandidateFieldKey.ADDITIONAL_INFORMATION.value: "Auto screening pass with assessment enabled.",
        }
    elif scenario_key == "no_assessment_auto_pass":
        scenario_overrides = {
            CandidateFieldKey.WHATSAPP.value: "+55 11 91000 0002",
            CandidateFieldKey.EDUCATION_STATUS.value: ("bachelor_completed", "bachelor_completed"),
            CandidateFieldKey.ADDITIONAL_INFORMATION.value: "Auto screening pass without assessment.",
        }
    elif scenario_key == "assessment_manual_pending":
        scenario_overrides = {
            CandidateFieldKey.WHATSAPP.value: "+55 11 91000 0003",
            CandidateFieldKey.EDUCATION_STATUS.value: ("bachelor_completed", "bachelor_completed"),
            CandidateFieldKey.ADDITIONAL_INFORMATION.value: "Assessment enabled without automation rules.",
        }
    elif scenario_key == "no_assessment_auto_rejected":
        scenario_overrides = {
            CandidateFieldKey.WHATSAPP.value: "+55 11 91000 0004",
            CandidateFieldKey.EDUCATION_STATUS.value: ("bachelor_completed", "bachelor_completed"),
            CandidateFieldKey.ADDITIONAL_INFORMATION.value: "Automation enabled without assessment. Candidate should be rejected.",
        }
    else:
        raise ValueError(f"Unsupported scenario: {scenario_key}")

    merged = {**base, **scenario_overrides}
    items: list[dict[str, Any]] = []
    for field_key, value in merged.items():
        if field_key == CandidateFieldKey.RESUME_ATTACHMENT.value:
            _, display_value = value
            items.append(
                {
                    "field_key": field_key,
                    "value": display_value,
                    "display_value": display_value,
                    "asset_id": resume_asset_id,
                }
            )
            continue
        if isinstance(value, tuple):
            raw_value, display_value = value
            items.append({"field_key": field_key, "value": raw_value, "display_value": display_value})
            continue
        items.append({"field_key": field_key, "value": value})
    return items


DEMO_JOB_DEFINITIONS = [
    {
        "key": "assessment_auto_pass",
        "title": "Progress Demo 1 - Assessment + Automation",
        "company_name": "Progress Lab A",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>Assessment enabled and automation enabled. Passing application should enter assessment review.</p>",
        "compensation_min": Decimal("8.00"),
        "compensation_max": Decimal("12.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": _build_rule_group(
            _build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="master_completed",
            )
        ),
        "expected_stage": "assessment_review",
    },
    {
        "key": "no_assessment_auto_pass",
        "title": "Progress Demo 2 - No Assessment + Automation Pass",
        "company_name": "Progress Lab B",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>Assessment disabled and automation enabled. Passing application should enter screening passed.</p>",
        "compensation_min": Decimal("10.00"),
        "compensation_max": Decimal("15.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": False,
        "automation_rules": _build_rule_group(
            _build_rule(
                field_key=CandidateFieldKey.COUNTRY_OF_RESIDENCE,
                operator="contains",
                value="Brazil",
            )
        ),
        "expected_stage": "screening_passed",
    },
    {
        "key": "assessment_manual_pending",
        "title": "Progress Demo 3 - Assessment + Manual Screening",
        "company_name": "Progress Lab C",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>Assessment enabled and no automation rules. Application should stay in pending screening.</p>",
        "compensation_min": Decimal("9.00"),
        "compensation_max": Decimal("13.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": True,
        "automation_rules": {"combinator": "and", "rules": []},
        "expected_stage": "pending_screening",
    },
    {
        "key": "no_assessment_auto_rejected",
        "title": "Progress Demo 4 - No Assessment + Automation Reject",
        "company_name": "Progress Lab D",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>Assessment disabled and automation enabled. This submission should be rejected by automation.</p>",
        "compensation_min": Decimal("7.00"),
        "compensation_max": Decimal("11.00"),
        "compensation_unit": "Per Hour",
        "assessment_enabled": False,
        "automation_rules": _build_rule_group(
            _build_rule(
                field_key=CandidateFieldKey.EDUCATION_STATUS,
                operator="contains",
                value="phd",
            )
        ),
        "expected_stage": "rejected",
    },
]


async def ensure_job(
    *,
    owner_admin_user_id: int,
    form_template_id: int,
    form_fields: list[dict[str, Any]],
    definition: dict[str, Any],
    assessment_mail_account_id: int | None = None,
    assessment_mail_template_id: int | None = None,
    assessment_mail_signature_id: int | None = None,
) -> Job:
    async with local_session() as session:
        result = await session.execute(
            select(Job).where(
                Job.title == definition["title"],
                Job.owner_admin_user_id == owner_admin_user_id,
                Job.is_deleted.is_(False),
            )
        )
        job = result.scalar_one_or_none()
        data = {
            JOB_DATA_FORM_FIELDS_KEY: form_fields,
            JOB_DATA_AUTOMATION_RULES_KEY: definition["automation_rules"],
        }
        if job is None:
            job = Job(
                title=definition["title"],
                company_name=definition["company_name"],
                country=definition["country"],
                status=JobStatus.OPEN.value,
                work_mode=definition["work_mode"],
                compensation_min=definition["compensation_min"],
                compensation_max=definition["compensation_max"],
                compensation_unit=definition["compensation_unit"],
                description=definition["description"],
                applicant_count=0,
                owner_admin_user_id=owner_admin_user_id,
                form_template_id=form_template_id,
                assessment_enabled=definition["assessment_enabled"],
                assessment_mail_account_id=assessment_mail_account_id,
                assessment_mail_template_id=assessment_mail_template_id,
                assessment_mail_signature_id=assessment_mail_signature_id,
                data=data,
            )
            session.add(job)
        else:
            job.company_name = definition["company_name"]
            job.country = definition["country"]
            job.status = JobStatus.OPEN.value
            job.work_mode = definition["work_mode"]
            job.compensation_min = definition["compensation_min"]
            job.compensation_max = definition["compensation_max"]
            job.compensation_unit = definition["compensation_unit"]
            job.description = definition["description"]
            job.form_template_id = form_template_id
            job.assessment_enabled = definition["assessment_enabled"]
            job.assessment_mail_account_id = assessment_mail_account_id
            job.assessment_mail_template_id = assessment_mail_template_id
            job.assessment_mail_signature_id = assessment_mail_signature_id
            job.data = data
            job.is_deleted = False
            job.deleted_at = None
        await session.commit()
        await session.refresh(job)
        return job


async def ensure_assessment_mail_dependencies(*, admin_user_id: int) -> dict[str, int]:
    async with local_session() as session:
        account_email = "flow-assessment@example.com"
        account_result = await session.execute(
            select(MailAccount).where(
                MailAccount.admin_user_id == admin_user_id,
                MailAccount.email == account_email,
                MailAccount.is_deleted.is_(False),
            )
        )
        account = account_result.scalar_one_or_none()
        preset = MAIL_ACCOUNT_PROVIDER_PRESETS[MailAccountProvider.QQ.value]
        if account is None:
            account = MailAccount(
                admin_user_id=admin_user_id,
                email=account_email,
                provider=MailAccountProvider.QQ.value,
                smtp_username=account_email,
                smtp_host=str(preset["smtp_host"]),
                smtp_port=int(preset["smtp_port"]),
                security_mode=str(preset["security_mode"]),
                auth_secret="flow-demo-auth-code",
                status=MailAccountStatus.ENABLED.value,
                note="Seeded for job progress assessment demo.",
            )
            session.add(account)
            await session.flush()

        category_result = await session.execute(
            select(MailTemplateCategory).where(
                MailTemplateCategory.admin_user_id == admin_user_id,
                MailTemplateCategory.name == "流程测试题",
                MailTemplateCategory.parent_id.is_(None),
                MailTemplateCategory.is_deleted.is_(False),
            )
        )
        category = category_result.scalar_one_or_none()
        if category is None:
            category = MailTemplateCategory(
                admin_user_id=admin_user_id,
                parent_id=None,
                name="流程测试题",
                sort_order=1,
                enabled=True,
            )
            session.add(category)
            await session.flush()

        template_result = await session.execute(
            select(MailTemplate).where(
                MailTemplate.admin_user_id == admin_user_id,
                MailTemplate.name == "流程测试题通知模板",
                MailTemplate.is_deleted.is_(False),
            )
        )
        template = template_result.scalar_one_or_none()
        if template is None:
            template = MailTemplate(
                admin_user_id=admin_user_id,
                category_id=category.id,
                name="流程测试题通知模板",
                subject_template="请完成 {{job_title}} 测试题",
                body_html="<p>Hi {{candidate_name}}，请完成 {{job_title}} 的测试题。</p>",
                attachments=[],
            )
            session.add(template)
            await session.flush()

        signature_result = await session.execute(
            select(MailSignature).where(
                MailSignature.admin_user_id == admin_user_id,
                MailSignature.name == "流程测试题签名",
                MailSignature.is_deleted.is_(False),
            )
        )
        signature = signature_result.scalar_one_or_none()
        if signature is None:
            signature = MailSignature(
                admin_user_id=admin_user_id,
                name="流程测试题签名",
                owner="Recruiting",
                enabled=True,
                full_name="Flow Admin",
                job_title="Recruiting Manager",
                company_name="T-Maxx",
                primary_email=account_email,
                secondary_email=None,
                website="https://www.t-maxx.cc",
                linkedin_label="T-Maxx",
                linkedin_url="https://www.linkedin.com/company/t-maxx",
                address="Beijing, China",
                avatar_asset_id=None,
                banner_asset_id=None,
            )
            session.add(signature)
            await session.flush()

        await session.commit()
        return {
            "mail_account_id": int(account.id),
            "mail_template_id": int(template.id),
            "mail_signature_id": int(signature.id),
        }


async def seed_admin_and_jobs() -> tuple[dict[str, Any], list[Job]]:
    async with local_session() as session:
        for definition in DICTIONARY_DEFINITIONS:
            await ensure_dictionary(session, definition)

        form_template = await ensure_form_template(session)
        role = await ensure_role(session)
        admin = await ensure_admin_user(session, role_id=role.id)
        await session.commit()
        form_fields = list(form_template.fields or [])

    assessment_mail_ids = await ensure_assessment_mail_dependencies(admin_user_id=admin.id)
    jobs: list[Job] = []
    for definition in DEMO_JOB_DEFINITIONS:
        jobs.append(
            await ensure_job(
                owner_admin_user_id=admin.id,
                form_template_id=form_template.id,
                form_fields=form_fields,
                definition=definition,
                assessment_mail_account_id=assessment_mail_ids["mail_account_id"] if definition["assessment_enabled"] else None,
                assessment_mail_template_id=assessment_mail_ids["mail_template_id"] if definition["assessment_enabled"] else None,
                assessment_mail_signature_id=assessment_mail_ids["mail_signature_id"] if definition["assessment_enabled"] else None,
            )
        )

    return {
        "admin": {
            "username": DEMO_ADMIN_USERNAME,
            "email": DEMO_ADMIN_EMAIL,
            "password": DEMO_ADMIN_PASSWORD,
        },
        "form_template": {
            "id": form_template.id,
            "name": form_template.name,
        },
    }, jobs


async def fetch_progress_payload(*, application_id: int) -> dict[str, Any]:
    async with local_session() as session:
        progress = await get_job_progress_by_application_id(application_id=application_id, db=session)
        if progress is None:
            raise RuntimeError(f"Missing job progress for application_id={application_id}")
        return serialize_job_progress(progress)


async def main() -> None:
    args = parse_args()
    if args.candidate_email == DEFAULT_CANDIDATE_EMAIL:
        timestamp = datetime.now().strftime("%m%d%H%M%S")
        local_part, _, domain = args.candidate_email.partition("@")
        trimmed_local = local_part[:10] or "cand"
        candidate_email = f"{trimmed_local}.{timestamp}@{domain or 'example.com'}"
    else:
        candidate_email = args.candidate_email.strip()
    try:
        seed_payload, jobs = await seed_admin_and_jobs()

        transport = ASGITransport(app=web_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=args.base_url.rstrip("/"),
            timeout=30.0,
        ) as client:
            await register_or_reuse_candidate(
                client,
                name=args.candidate_name,
                email=candidate_email,
                password=args.candidate_password,
            )
            access_token = await login_candidate(
                client,
                email=candidate_email,
                password=args.candidate_password,
            )
            current_user = await fetch_current_user(client, access_token=access_token)
            resume_asset = await ensure_resume_asset(user_id=int(current_user["id"]), email=candidate_email)

            applications: list[dict[str, Any]] = []
            for definition, job in zip(DEMO_JOB_DEFINITIONS, jobs, strict=True):
                items = build_application_items(
                    scenario_key=definition["key"],
                    candidate_name=args.candidate_name,
                    candidate_email=candidate_email,
                    resume_asset_id=resume_asset.id,
                )
                apply_payload = await submit_application(
                    client,
                    access_token=access_token,
                    job_id=job.id,
                    items=items,
                )
                progress_payload = await fetch_progress_payload(application_id=int(apply_payload["application_id"]))
                applications.append(
                    {
                        "job_id": job.id,
                        "job_title": job.title,
                        "company_name": job.company_name,
                        "assessment_enabled": job.assessment_enabled,
                        "automation_rules_enabled": bool((job.data or {}).get(JOB_DATA_AUTOMATION_RULES_KEY, {}).get("rules")),
                        "application_id": apply_payload["application_id"],
                        "talent_profile_id": apply_payload["talent_profile_id"],
                        "expected_stage": definition["expected_stage"],
                        "current_stage": progress_payload["current_stage"],
                        "current_stage_cn_name": progress_payload["current_stage_cn_name"],
                        "screening_mode": progress_payload["screening_mode"],
                    }
                )

        payload = {
            "admin": seed_payload["admin"],
            "candidate": {
                "name": args.candidate_name,
                "email": candidate_email,
                "password": args.candidate_password,
                "user_id": int(current_user["id"]),
                "resume_asset_id": resume_asset.id,
            },
            "form_template": seed_payload["form_template"],
            "jobs": applications,
            "note": (
                "第 4 个岗位按“无测试题 + 自动化流程”，并让投递不命中规则，用来保留一条淘汰数据，"
                "因为你给的第 2 和第 4 项配置描述重复。"
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
