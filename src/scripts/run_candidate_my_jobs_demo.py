import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from httpx import ASGITransport
from sqlalchemy import or_, select

from ..app.core.db.database import local_session
from ..app.main_admin import app as admin_app
from ..app.main_web import app as web_app
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
from ..app.modules.admin.mail_task.model import MailTask
from ..app.modules.user.model import User
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare candidate-facing My Jobs demo data.")
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
        "title": "Candidate Portal Demo - Fresh Apply Flow",
        "company_name": "TMX Fresh Flow Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role is intentionally left without an application so the candidate can test the end-to-end flow from the public jobs page.</p>",
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
        "key": "pending_screening",
        "title": "Candidate Portal Demo - Pending Screening",
        "company_name": "TMX Pending Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role stays in pending screening so the candidate can verify the passive waiting state in My Jobs.</p>",
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
        "key": "assessment_review",
        "title": "Candidate Portal Demo - Assessment Review",
        "company_name": "TMX Assessment Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role lands in assessment review and keeps the upload action available in the candidate portal.</p>",
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
        "application_scenario": "assessment_auto_pass",
        "target_stage": "assessment_review",
        "auto_apply": True,
    },
    {
        "key": "screening_passed",
        "title": "Candidate Portal Demo - Screening Passed",
        "company_name": "TMX Screening Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role lands in screening passed so the candidate can see a no-action, in-progress state.</p>",
        "compensation_min": Decimal("10.00"),
        "compensation_max": Decimal("14.00"),
        "compensation_unit": "Per Day",
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
        "key": "contract_pool",
        "title": "Candidate Portal Demo - Contract Pool",
        "company_name": "TMX Contract Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role reaches contract pool so the candidate can review the draft contract and upload a signed copy.</p>",
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
        "target_stage": "contract_pool",
        "auto_apply": True,
    },
    {
        "key": "active",
        "title": "Candidate Portal Demo - Active",
        "company_name": "TMX Active Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role reaches active so the candidate can verify the active placement state.</p>",
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
        "target_stage": "active",
        "auto_apply": True,
    },
    {
        "key": "rejected",
        "title": "Candidate Portal Demo - Rejected",
        "company_name": "TMX Reject Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role is rejected by automation so the candidate can verify the closed state.</p>",
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
    {
        "key": "replaced",
        "title": "Candidate Portal Demo - Replaced",
        "company_name": "TMX Replace Lab",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<p>This role reaches replaced so the candidate can verify the final replaced state.</p>",
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
        "target_stage": "replaced",
        "auto_apply": True,
    },
]


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
        {"field_key": CandidateFieldKey.NATIONALITY.value, "value": "Brazilian"},
        {"field_key": CandidateFieldKey.NATIVE_LANGUAGES.value, "value": "Portuguese"},
        {"field_key": CandidateFieldKey.ADDITIONAL_LANGUAGES.value, "value": "English"},
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

    mail_ids = await ensure_assessment_mail_dependencies(admin_user_id=admin.id)
    rejection_mail_ids = await ensure_rejection_mail_dependencies(admin_user_id=admin.id)
    jobs: list[Job] = []
    for definition in PORTAL_JOB_DEFINITIONS:
        rejection_enabled = str(definition["key"]) == "rejected"
        async with local_session() as session:
            company = await ensure_company(session, name=definition["company_name"])
            project = await ensure_company_project(
                session,
                company_id=company.id,
                name=definition.get("project_name", "Default Project"),
            )
            result = await session.execute(
                select(Job).where(
                    Job.title == definition["title"],
                    Job.owner_admin_user_id == admin.id,
                    Job.is_deleted.is_(False),
                )
            )
            job = result.scalar_one_or_none()
            data = {
                JOB_DATA_FORM_FIELDS_KEY: form_fields,
                JOB_DATA_AUTOMATION_RULES_KEY: definition["automation_rules"],
                JOB_DATA_CONTRACT_EXAMPLE_KEY: definition.get("contract_example")
                or build_contract_example_html(
                    job_title=definition["title"],
                    company_name=company.name,
                    compensation_unit=str(definition["compensation_unit"]),
                ),
            }
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
                    assessment_enabled=definition["assessment_enabled"],
                    assessment_mail_account_id=mail_ids["mail_account_id"] if definition["assessment_enabled"] else None,
                    assessment_mail_template_id=mail_ids["mail_template_id"] if definition["assessment_enabled"] else None,
                    assessment_mail_signature_id=mail_ids["mail_signature_id"] if definition["assessment_enabled"] else None,
                    data=data,
                )
                session.add(job)
            else:
                job.company_id = company.id
                job.project_id = project.id
                job.country = definition["country"]
                job.status = JobStatus.OPEN.value
                job.work_mode = definition["work_mode"]
                job.compensation_min = definition["compensation_min"]
                job.compensation_max = definition["compensation_max"]
                job.compensation_unit = definition["compensation_unit"]
                job.description = definition["description"]
                job.form_template_id = form_template.id
                job.assessment_enabled = definition["assessment_enabled"]
                job.assessment_mail_account_id = mail_ids["mail_account_id"] if definition["assessment_enabled"] else None
                job.assessment_mail_template_id = mail_ids["mail_template_id"] if definition["assessment_enabled"] else None
                job.assessment_mail_signature_id = mail_ids["mail_signature_id"] if definition["assessment_enabled"] else None
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
        }
    }, jobs


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
    response = await client.patch(
        f"/jobs/{job_id}/progress/contract-record",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "progress_ids": progress_ids,
            "agreement_ref_no": agreement_ref_no,
            "signing_status": signing_status,
            "contract_review": contract_review,
            "rate": rate,
        },
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


async def reset_candidate_portal_demo_state(*, user_id: int, job_ids: list[int]) -> dict[str, int]:
    now = datetime.now(UTC)
    async with local_session() as session:
        application_result = await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.user_id == user_id,
                CandidateApplication.job_id.in_(job_ids),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        applications = list(application_result.scalars().all())
        application_ids = [int(application.id) for application in applications]

        progress_result = await session.execute(
            select(JobProgress).where(
                JobProgress.user_id == user_id,
                JobProgress.job_id.in_(job_ids),
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
        if job_ids:
            scoped_contract_conditions.append(ContractRecord.job_id.in_(job_ids))
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

        await session.commit()
        return {
            "applications": len(applications),
            "progresses": len(progresses),
            "contracts": len(contracts),
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
    payload = await fetch_my_applications_page(client, access_token=access_token)
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
    payload = ensure_ok(response, "List My Jobs failed")
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


async def main() -> None:
    args = parse_args()

    print_step("Step 1/6: seed admin, one fresh job, and seven candidate demo jobs")
    seed_payload, jobs = await ensure_candidate_portal_jobs()
    for job in jobs:
        print_detail(f"job ready: {job.id} {job.title}")

    async with httpx.AsyncClient(
        transport=ASGITransport(app=web_app),
        base_url=WEB_BASE_URL,
        timeout=30.0,
    ) as web_client, httpx.AsyncClient(
        transport=ASGITransport(app=admin_app),
        base_url=ADMIN_BASE_URL,
        timeout=30.0,
    ) as admin_client:
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
            job_ids=[int(job.id) for job in jobs],
        )
        print_detail(
            "reset prior portal demo state: "
            f"applications={reset_summary['applications']} "
            f"progresses={reset_summary['progresses']} "
            f"contracts={reset_summary['contracts']}"
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

        duplicate_job = next(job for definition, job in zip(PORTAL_JOB_DEFINITIONS, jobs, strict=True) if should_auto_apply(definition))
        duplicate_items = build_application_items(
            scenario_key=str(next(definition["application_scenario"] for definition in PORTAL_JOB_DEFINITIONS if should_auto_apply(definition))),
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
                f"Duplicate application should fail with 400, got {duplicate_response.status_code} {duplicate_response.text}"
            )
        print_detail(f"duplicate apply blocked for job_id={duplicate_job.id}: {duplicate_response.json().get('detail')}")

        auto_subjects = _subjects_for_recipient(await list_mail_tasks(), args.candidate_email)
        if "请完成 {{job_title}} 测试题" not in auto_subjects:
            raise RuntimeError("Missing auto-created assessment mail task in candidate-portal demo flow.")
        if "关于 {{job_title}} 的申请结果通知" not in auto_subjects:
            raise RuntimeError("Missing auto-created rejection mail task in candidate-portal demo flow.")
        print_detail("auto mail tasks created for assessment-review and rejected branches")

        print_step("Step 4/6: shape the seven applications into seven different stages")
        list_payload = await fetch_my_applications(web_client, access_token=access_token)
        by_application_id = {int(item["application_id"]): item for item in list_payload}
        for case in cases_by_key.values():
            item = by_application_id[case["application_id"]]
            case["detail"] = item
            case["job_progress_id"] = int(item["job_progress_id"])

        assessment_case = cases_by_key["assessment_review"]
        await upload_assessment(
            web_client,
            access_token=access_token,
            job_id=int(assessment_case["job"].id),
            file_name="candidate-assessment.xlsx",
            file_bytes=build_demo_xlsx_bytes(
                candidate_email=args.candidate_email,
                note="Candidate portal assessment submission.",
            ),
        )
        print_detail("assessment_review job received one candidate submission")

        screening_case = cases_by_key["screening_passed"]
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(screening_case["job"].id),
            progress_ids=[int(screening_case["job_progress_id"])],
            agreement_ref_no="SCREEN-2026-001",
            signing_status="已通知人选签合同",
            rate="4.20",
        )
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(screening_case["job"].id),
            progress_id=int(screening_case["job_progress_id"]),
            file_name="Screen 2026 001 Draft Contract.pdf",
        )
        print_detail("screening_passed job now has a ready-to-sign draft contract")

        contract_case = cases_by_key["contract_pool"]
        contract_case_stage = str(contract_case["detail"]["current_stage"])
        contract_case_record = dict(contract_case["detail"].get("contract_record_data") or {})
        if contract_case_stage not in {"screening_passed", "contract_pool"}:
            raise RuntimeError(f"Contract-pool demo expected screening_passed/contract_pool, got {contract_case_stage}")
        contract_case_update_kwargs: dict[str, Any] = {
            "agreement_ref_no": "POOL-2026-002",
            "rate": "6.80",
        }
        if contract_case_stage == "screening_passed":
            contract_case_update_kwargs["signing_status"] = "已通知人选签合同"

        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(contract_case["job"].id),
            progress_ids=[int(contract_case["job_progress_id"])],
            **contract_case_update_kwargs,
        )

        if not contract_case_record.get("draft_contract_attachment"):
            failed_signed_upload = await candidate_upload_signed_contract_response(
                web_client,
                access_token=access_token,
                job_id=int(contract_case["job"].id),
                file_name="candidate-signed-before-draft.docx",
            )
            if failed_signed_upload.status_code != 400:
                raise RuntimeError(
                    "Signed contract upload should fail before the draft contract exists, "
                    f"got {failed_signed_upload.status_code} {failed_signed_upload.text}"
                )
            print_detail(
                f"signed contract upload blocked before draft: {failed_signed_upload.json().get('detail')}"
            )
        await admin_upload_contract_draft(
            admin_client,
            access_token=admin_access_token,
            job_id=int(contract_case["job"].id),
            progress_id=int(contract_case["job_progress_id"]),
            file_name="Pool 2026 002 Draft Contract.pdf",
        )
        if contract_case_record.get("candidate_signed_contract_attachment") and contract_case_record.get("contract_review") != "待修改":
            await admin_update_contract_record(
                admin_client,
                access_token=admin_access_token,
                job_id=int(contract_case["job"].id),
                progress_ids=[int(contract_case["job_progress_id"])],
                contract_review="待修改",
            )
        successful_signed_upload = await candidate_upload_signed_contract_response(
            web_client,
            access_token=access_token,
            job_id=int(contract_case["job"].id),
            file_name="candidate-signed-contract-pool.docx",
        )
        if successful_signed_upload.status_code not in {200, 201}:
            raise RuntimeError(
                "Signed contract upload should succeed after the draft exists, "
                f"got {successful_signed_upload.status_code} {successful_signed_upload.text}"
            )
        await admin_update_contract_record(
            admin_client,
            access_token=admin_access_token,
            job_id=int(contract_case["job"].id),
            progress_ids=[int(contract_case["job_progress_id"])],
            contract_review="待修改",
        )
        print_detail("contract_pool job moved, draft uploaded, signed contract submitted, and review reset to 待修改")

        active_case = cases_by_key["active"]
        active_case_stage = str(active_case["detail"]["current_stage"])
        active_case_record = dict(active_case["detail"].get("contract_record_data") or {})
        if active_case_stage == "active" and active_case_record.get("company_sealed_contract_attachment"):
            refreshed_asset_id = await refresh_active_contract_attachment(
                progress_id=int(active_case["job_progress_id"]),
                admin_user_id=int(admin_login_payload["user"]["id"]),
            )
            print_detail(
                "active job already has company-signed contract data and remains active"
                + (f"; refreshed attachment asset_id={refreshed_asset_id}" if refreshed_asset_id else "")
            )
        else:
            if active_case_stage not in {"screening_passed", "contract_pool"}:
                raise RuntimeError(f"Active demo expected screening_passed/contract_pool/active, got {active_case_stage}")
            active_case_update_kwargs: dict[str, Any] = {
                "agreement_ref_no": "ACTIVE-2026-003",
                "rate": "3500",
            }
            if active_case_stage == "screening_passed":
                active_case_update_kwargs["signing_status"] = "已通知人选签合同"
            await admin_update_contract_record(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_ids=[int(active_case["job_progress_id"])],
                **active_case_update_kwargs,
            )
            await admin_upload_contract_draft(
                admin_client,
                access_token=admin_access_token,
                job_id=int(active_case["job"].id),
                progress_id=int(active_case["job_progress_id"]),
                file_name="Active 2026 003 Draft Contract.pdf",
            )
            if active_case_record.get("candidate_signed_contract_attachment") and active_case_record.get("contract_review") != "待修改":
                await admin_update_contract_record(
                    admin_client,
                    access_token=admin_access_token,
                    job_id=int(active_case["job"].id),
                    progress_ids=[int(active_case["job_progress_id"])],
                    contract_review="待修改",
                )
            active_signed_upload = await candidate_upload_signed_contract_response(
                web_client,
                access_token=access_token,
                job_id=int(active_case["job"].id),
                file_name="candidate-signed-contract-active.docx",
            )
            if active_signed_upload.status_code not in {200, 201}:
                raise RuntimeError(
                    "Active-path signed contract upload should succeed, "
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
                file_name="Active 2026 003 Company Returned Contract.pdf",
            )
            print_detail("active job now has draft, signed, company-returned contracts and is active")

        replaced_case = cases_by_key["replaced"]
        replaced_case_stage = str(replaced_case["detail"]["current_stage"])
        if replaced_case_stage == "replaced":
            print_detail("replaced job already sits in replaced stage")
        else:
            if replaced_case_stage not in {"screening_passed", "contract_pool", "active"}:
                raise RuntimeError(f"Replaced demo expected screening_passed/contract_pool/active/replaced, got {replaced_case_stage}")
            if replaced_case_stage != "active":
                replaced_case_update_kwargs: dict[str, Any] = {
                    "agreement_ref_no": "REPLACED-2026-004",
                    "rate": "5.50",
                }
                if replaced_case_stage == "screening_passed":
                    replaced_case_update_kwargs["signing_status"] = "已通知人选签合同"
                await admin_update_contract_record(
                    admin_client,
                    access_token=admin_access_token,
                    job_id=int(replaced_case["job"].id),
                    progress_ids=[int(replaced_case["job_progress_id"])],
                    **replaced_case_update_kwargs,
                )
                replaced_case_record = dict(replaced_case["detail"].get("contract_record_data") or {})
                if not replaced_case_record.get("draft_contract_attachment"):
                    await admin_upload_contract_draft(
                        admin_client,
                        access_token=admin_access_token,
                        job_id=int(replaced_case["job"].id),
                        progress_id=int(replaced_case["job_progress_id"]),
                        file_name="Replaced 2026 004 Draft Contract.pdf",
                    )
                if replaced_case_record.get("candidate_signed_contract_attachment") and replaced_case_record.get("contract_review") != "待修改":
                    await admin_update_contract_record(
                        admin_client,
                        access_token=admin_access_token,
                        job_id=int(replaced_case["job"].id),
                        progress_ids=[int(replaced_case["job_progress_id"])],
                        contract_review="待修改",
                    )
                replaced_signed_upload = await candidate_upload_signed_contract_response(
                    web_client,
                    access_token=access_token,
                    job_id=int(replaced_case["job"].id),
                    file_name="candidate-signed-contract-replaced.docx",
                )
                if replaced_signed_upload.status_code not in {200, 201}:
                    raise RuntimeError(
                        "Replaced-path signed contract upload should succeed, "
                        f"got {replaced_signed_upload.status_code} {replaced_signed_upload.text}"
                    )
                await admin_update_contract_record(
                    admin_client,
                    access_token=admin_access_token,
                    job_id=int(replaced_case["job"].id),
                    progress_ids=[int(replaced_case["job_progress_id"])],
                    contract_review="审核通过",
                )
                await admin_upload_company_sealed_contract(
                    admin_client,
                    access_token=admin_access_token,
                    job_id=int(replaced_case["job"].id),
                    progress_id=int(replaced_case["job_progress_id"]),
                    file_name="Replaced 2026 004 Company Returned Contract.pdf",
                )
            await admin_move_stage(
                admin_client,
                access_token=admin_access_token,
                job_id=int(replaced_case["job"].id),
                progress_ids=[int(replaced_case["job_progress_id"])],
                target_stage="replaced",
                reason="candidate_portal_demo_replaced",
            )
            print_detail("replaced job moved to replaced")

        print_step("Step 5/6: verify My Jobs list and candidate-facing detail APIs")
        refreshed_items = await fetch_my_applications(web_client, access_token=access_token)
        refreshed_by_title = {str(item["job_title"]): item for item in refreshed_items}
        expected_titles = {str(definition["title"]) for definition in PORTAL_JOB_DEFINITIONS if should_auto_apply(definition)}
        missing_titles = expected_titles.difference(refreshed_by_title.keys())
        if missing_titles:
            raise RuntimeError(f"My Jobs missing titles: {sorted(missing_titles)}")
        for definition in PORTAL_JOB_DEFINITIONS:
            if not should_auto_apply(definition):
                continue
            item = refreshed_by_title[str(definition["title"])]
            expected_stage = str(definition["target_stage"])
            if str(item["current_stage"]) != expected_stage:
                raise RuntimeError(
                    f"Job {definition['title']} expected stage={expected_stage}, got {item['current_stage']}"
                )
            print_detail(f"{definition['title']} -> {item['current_stage']}")

        contract_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["contract_pool"]["application_id"]),
        )
        screening_contract_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["screening_passed"]["application_id"]),
        )
        active_contract_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["active"]["application_id"]),
        )
        assessment_detail = await fetch_my_application_detail(
            web_client,
            access_token=access_token,
            application_id=int(cases_by_key["assessment_review"]["application_id"]),
        )
        print_detail(
            f"assessment detail submissions={len(assessment_detail.get('process_data', {}).get('assessment_submissions', []))}"
        )
        print_detail(
            f"contract detail draft={bool((contract_detail.get('contract_record_data') or {}).get('draft_contract_attachment'))}"
        )
        if not screening_contract_detail.get("contract_example_html"):
            raise RuntimeError("Screening-passed contract detail is missing the contract example content.")
        if not (screening_contract_detail.get("contract_record_data") or {}).get("draft_contract_attachment"):
            raise RuntimeError("Screening-passed contract detail is missing its draft contract.")
        if (contract_detail.get("contract_record_data") or {}).get("contract_review") != "待修改":
            raise RuntimeError("Contract-pool detail should be in 待修改 review status for re-upload testing.")
        if not (active_contract_detail.get("contract_record_data") or {}).get("company_sealed_contract_attachment"):
            raise RuntimeError("Active contract detail is missing the company returned contract attachment.")
        if (active_contract_detail.get("contract_record_data") or {}).get("contract_status") != "Active":
            raise RuntimeError("Active contract detail should already be marked Active.")
        draft_download_url = (
            (screening_contract_detail.get("contract_record_data") or {})
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
            str(cases_by_key["screening_passed"]["job"].title),
            str(cases_by_key["contract_pool"]["job"].title),
            str(cases_by_key["active"]["job"].title),
        }
        missing_contract_titles = expected_contract_titles.difference(my_contract_titles.keys())
        if missing_contract_titles:
            raise RuntimeError(f"My Contracts missing titles: {sorted(missing_contract_titles)}")
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
            raise RuntimeError("My Jobs page_size=2 returned more than two items.")
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
        if not needs_action_payload.get("items"):
            raise RuntimeError("Needs-action filter returned no items.")
        if any(item.get("current_stage") not in {"assessment_review", "screening_passed", "contract_pool"} for item in needs_action_payload["items"]):
            raise RuntimeError("Needs-action filter returned a non-action stage.")
        needs_action_titles = {str(item["job_title"]) for item in needs_action_payload["items"]}
        expected_needs_action_titles = {
            str(cases_by_key["assessment_review"]["job"].title),
            str(cases_by_key["screening_passed"]["job"].title),
            str(cases_by_key["contract_pool"]["job"].title),
        }
        if expected_needs_action_titles.difference(needs_action_titles):
            raise RuntimeError(
                "Needs-action filter is missing expected workspaces: "
                f"{sorted(expected_needs_action_titles.difference(needs_action_titles))}"
            )
        print_detail(f"needs-action filter works: items={len(needs_action_payload['items'])}")

        print_step("Step 6/6: ready-to-test summary")
        print(f"candidate email: {args.candidate_email}")
        print(f"candidate password: {args.candidate_password}")
        fresh_job = next(job for definition, job in zip(PORTAL_JOB_DEFINITIONS, jobs, strict=True) if not should_auto_apply(definition))
        print(f"fresh job title: {fresh_job.title}")
        print(f"fresh job id: {fresh_job.id}")
        print("my jobs summary:")
        for definition in PORTAL_JOB_DEFINITIONS:
            if not should_auto_apply(definition):
                continue
            item = refreshed_by_title[str(definition["title"])]
            print(f"  - {definition['title']}: {item['current_stage']}")
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
