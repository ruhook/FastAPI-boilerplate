import argparse
import asyncio
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from ..app.core.db.database import local_session
from ..app.main_admin import app as admin_app
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.company.model import AdminCompany
from ..app.modules.assets.model import Asset
from ..app.modules.assets.schema import AssetUploadPayload
from ..app.modules.assets.service import create_asset_from_bytes, read_asset_content, store_asset_content
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.candidate_application.schema import CandidateApplicationSubmitRequest
from ..app.modules.candidate_field.const import CandidateFieldKey
from ..app.modules.contract_record.service import upsert_contract_record_for_progress
from ..app.modules.job.const import JobStatus
from ..app.modules.job.model import Job
from ..app.modules.job_progress.const import JobProgressDataKey, RecruitmentStage
from ..app.modules.job_progress.service import get_job_progress_by_application_id
from ..app.modules.project_timesheet_record.schema import (
    ProjectTimesheetBatchCreateEntry,
    ProjectTimesheetBatchCreateRequest,
)
from ..app.modules.project_timesheet_record.service import create_project_timesheet_records
from ..app.modules.talent_profile.model import TalentProfile
from ..app.modules.talent_profile.service import create_application_and_sync_talent
from .demo_assets import build_demo_resume_pdf_bytes, refresh_demo_resume_asset_if_needed
from .seed_apply_demo_flow import (
    DICTIONARY_DEFINITIONS,
    build_contract_example_html,
    ensure_dictionary,
    ensure_form_template,
    ensure_job,
)
from .seed_job_progress_demo_flow import ensure_superadmin_user
from .seed_timesheet_demo_flow import (
    ensure_active_contract_record,
    ensure_application,
    ensure_candidate_user,
    ensure_company_timesheet_config,
    ensure_job_progress,
    ensure_talent_profile,
    sync_job_applicant_count,
)

DEFAULT_BASE_URL = "http://testserver/api/v1"
DEFAULT_PROGRESS_CANDIDATE_COUNT = 240
DEFAULT_TIMESHEET_WORKER_COUNT = 60
DEFAULT_TIMESHEET_BATCH_COUNT = 18
DEFAULT_TIMESHEET_BATCH_SIZE = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed bulk advanced-filter demo data and verify admin APIs.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Admin API base URL.")
    parser.add_argument("--progress-count", type=int, default=DEFAULT_PROGRESS_CANDIDATE_COUNT)
    parser.add_argument("--timesheet-workers", type=int, default=DEFAULT_TIMESHEET_WORKER_COUNT)
    parser.add_argument("--timesheet-batches", type=int, default=DEFAULT_TIMESHEET_BATCH_COUNT)
    parser.add_argument("--timesheet-batch-size", type=int, default=DEFAULT_TIMESHEET_BATCH_SIZE)
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional JSON report path. Defaults to hr-server/tmp/advanced-filter-bulk-<run_tag>.json",
    )
    return parser.parse_args()


def print_step(message: str) -> None:
    print(f"\n=== {message} ===", flush=True)


def print_detail(message: str) -> None:
    print(f"  - {message}", flush=True)


def ensure_ok(response: httpx.Response, message: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{message}: {response.status_code} {response.text}")
    return response.json()


def make_run_tag() -> str:
    return f"AFB-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"


def make_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def make_compact_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    return normalized[-10:] or "bulkdemo"


def cycle(items: list[str], index: int) -> str:
    return items[(index - 1) % len(items)]


async def ensure_candidate_resume_asset(
    session,
    *,
    user_id: int,
    email: str,
) -> Asset:
    result = await session.execute(
        select(Asset).where(
            Asset.owner_type == "user",
            Asset.owner_id == user_id,
            Asset.module == "candidate_application",
            Asset.type == "file",
            Asset.original_name == "demo-resume.pdf",
            Asset.is_deleted.is_(False),
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        refresh_demo_resume_asset_if_needed(
            existing,
            email=email,
            generated_by="run_advanced_filter_bulk_demo.py",
            read_content=read_asset_content,
            store_content=store_asset_content,
        )
        return existing

    content = build_demo_resume_pdf_bytes(email=email, generated_by="run_advanced_filter_bulk_demo.py")
    return await create_asset_from_bytes(
        db=session,
        payload=AssetUploadPayload(
            type="file",
            module="candidate_application",
            owner_type="user",
            owner_id=user_id,
        ),
        original_name="demo-resume.pdf",
        content=content,
        mime_type="application/pdf",
        data={"generated_by": "run_advanced_filter_bulk_demo"},
    )


def build_progress_candidate_definition(*, run_tag: str, index: int) -> dict[str, Any]:
    compact = make_compact_token(run_tag)
    countries = ["Brazil", "Singapore", "Japan", "Mexico"]
    nationalities = ["Brazil", "Singapore", "Japan", "Mexico"]
    native_languages = ["Portuguese", "English", "Japanese", "Spanish"]
    additional_languages = [
        ["English", "Spanish"],
        ["English"],
        ["Japanese", "English"],
        ["French"],
        ["Spanish"],
        ["English", "Portuguese"],
    ]
    education_statuses = [
        "bachelor_completed",
        "master_completed",
        "high_school_completed",
    ]
    english_proficiencies = [
        "native_speaker",
        "fully_professional_proficiency",
        "intermediate_level",
        "basic_level",
    ]
    expected_salaries = ["2_5", "6_10", "11_15"]
    experience_levels = ["0_3_months", "1_2_years", "over_3_years"]
    job_sources = ["linkedin_job_post", "indeed_job_post", "referral_from_current_annotator"]

    return {
        "username": f"afp{compact}{index:03d}"[:20],
        "email": f"{make_slug(run_tag)}.progress.{index:03d}@example.com",
        "display_name": f"{run_tag} P{index:03d}",
        "full_name": f"{run_tag} Progress Candidate {index:03d}",
        "country_of_residence": cycle(countries, index),
        "nationality": cycle(nationalities, index),
        "native_languages": cycle(native_languages, index),
        "additional_languages": cycle(additional_languages, index),
        "english_proficiency": cycle(english_proficiencies, index),
        "education_status": cycle(education_statuses, index),
        "expected_salary": cycle(expected_salaries, index),
        "experience": cycle(experience_levels, index),
        "job_source": cycle(job_sources, index),
        "whatsapp": f"+65 8800 {index:04d}",
        "accepts_hourly_payment": "yes",
        "age_range": "26_30" if index % 2 else "31_35",
        "max_working_hours": "4_8_hours" if index % 4 else "over_8_hours",
        "requires_visa": "no_sponsorship_required",
        "note": "priority review" if index % 5 == 0 else "",
    }


def build_progress_application_items(
    *,
    run_tag: str,
    definition: dict[str, Any],
    resume_asset_id: int,
) -> list[dict[str, Any]]:
    return [
        {"field_key": CandidateFieldKey.FULL_NAME.value, "value": definition["full_name"]},
        {"field_key": CandidateFieldKey.EMAIL.value, "value": definition["email"]},
        {"field_key": CandidateFieldKey.WHATSAPP.value, "value": definition["whatsapp"]},
        {"field_key": CandidateFieldKey.COUNTRY_OF_RESIDENCE.value, "value": definition["country_of_residence"]},
        {"field_key": CandidateFieldKey.NATIONALITY.value, "value": definition["nationality"]},
        {"field_key": CandidateFieldKey.NATIVE_LANGUAGES.value, "value": definition["native_languages"]},
        {"field_key": CandidateFieldKey.ADDITIONAL_LANGUAGES.value, "value": definition["additional_languages"]},
        {
            "field_key": CandidateFieldKey.ENGLISH_PROFICIENCY.value,
            "value": definition["english_proficiency"],
            "display_value": definition["english_proficiency"],
        },
        {
            "field_key": CandidateFieldKey.AGE_RANGE.value,
            "value": definition["age_range"],
            "display_value": definition["age_range"],
        },
        {
            "field_key": CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value,
            "value": definition["max_working_hours"],
            "display_value": definition["max_working_hours"],
        },
        {
            "field_key": CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value,
            "value": definition["accepts_hourly_payment"],
            "display_value": definition["accepts_hourly_payment"],
        },
        {
            "field_key": CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value,
            "value": definition["expected_salary"],
            "display_value": definition["expected_salary"],
        },
        {
            "field_key": CandidateFieldKey.EDUCATION_STATUS.value,
            "value": definition["education_status"],
            "display_value": definition["education_status"],
        },
        {
            "field_key": CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE.value,
            "value": definition["experience"],
            "display_value": definition["experience"],
        },
        {
            "field_key": CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP.value,
            "value": definition["requires_visa"],
            "display_value": definition["requires_visa"],
        },
        {
            "field_key": CandidateFieldKey.RESUME_ATTACHMENT.value,
            "value": "demo-resume.pdf",
            "display_value": "demo-resume.pdf",
            "asset_id": resume_asset_id,
        },
        {
            "field_key": CandidateFieldKey.JOB_SOURCE.value,
            "value": definition["job_source"],
            "display_value": definition["job_source"],
        },
        {
            "field_key": CandidateFieldKey.ADDITIONAL_INFORMATION.value,
            "value": f"{run_tag} progress bulk seed {definition['display_name']}",
        },
    ]


def build_timesheet_worker_definition(*, run_tag: str, index: int) -> dict[str, Any]:
    compact = make_compact_token(run_tag)
    nationalities = ["Brazil", "Japan", "Singapore", "Mexico", "Egypt", "France"]
    locations = ["Sao Paulo", "Tokyo", "Singapore", "Monterrey", "Cairo", "Paris"]
    return {
        "index": index,
        "username": f"aft{compact}{index:03d}"[:20],
        "email": f"{make_slug(run_tag)}.worker.{index:03d}@example.com",
        "display_name": f"{run_tag} W{index:03d}",
        "full_name": f"{run_tag} Timesheet Worker {index:03d}",
        "nationality": cycle(nationalities, index),
        "location": cycle(locations, index),
        "rate": Decimal("8.50") + Decimal(index % 9),
    }


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


async def seed_progress_candidates(
    *,
    run_tag: str,
    count: int,
    admin_user_id: int,
    progress_job: Job,
) -> list[dict[str, Any]]:
    seeded: list[dict[str, Any]] = []
    base_now = datetime.now(UTC)
    async with local_session() as session:
        for index in range(1, count + 1):
            definition = build_progress_candidate_definition(run_tag=run_tag, index=index)
            user = await ensure_candidate_user(session, definition)
            resume_asset = await ensure_candidate_resume_asset(
                session,
                user_id=int(user.id),
                email=definition["email"],
            )
            payload = CandidateApplicationSubmitRequest(
                items=build_progress_application_items(
                    run_tag=run_tag,
                    definition=definition,
                    resume_asset_id=int(resume_asset.id),
                )
            )
            result = await create_application_and_sync_talent(
                job_id=int(progress_job.id),
                payload=payload,
                current_user={"id": int(user.id), "name": user.name, "email": user.email},
                db=session,
            )
            application = await session.get(CandidateApplication, int(result["application_id"]))
            talent = await session.get(TalentProfile, int(result["talent_profile_id"]))
            progress = await get_job_progress_by_application_id(application_id=int(application.id), db=session)
            if application is None or talent is None or progress is None:
                raise RuntimeError("Failed to build progress seed chain.")

            submitted_at = base_now - timedelta(days=index)
            application.submitted_at = submitted_at
            talent.latest_applied_at = submitted_at
            talent.created_at = submitted_at - timedelta(hours=2)
            talent.note = definition["note"] or None
            if index % 7 == 0:
                talent.resume_asset_id = None

            if index <= 80:
                stage_alias = "screening"
                progress.current_stage = RecruitmentStage.PENDING_SCREENING.value
                progress.data = {
                    **(progress.data or {}),
                    JobProgressDataKey.NOTE.value: definition["note"] or "",
                }
                contract_number = ""
                contract_review = ""
                accepted_rate = ""
            elif index <= 140:
                stage_alias = "passed"
                progress.current_stage = RecruitmentStage.SCREENING_PASSED.value
                contract = await upsert_contract_record_for_progress(
                    progress=progress,
                    job=progress_job,
                    db=session,
                    admin_user_id=admin_user_id,
                    field_updates={
                        "agreement_ref_no": f"{run_tag}-SP-{index:03d}",
                        "rate": Decimal("0.85") + Decimal(index % 4) / Decimal("10"),
                    },
                    data_updates={
                        "signing_status": "待筛选签约资料",
                    },
                )
                contract_number = str(contract.agreement_ref_no or "")
                contract_review = str((contract.data or {}).get("contract_review") or "")
                accepted_rate = str(contract.rate or "")
            elif index <= 200:
                stage_alias = "contract"
                progress.current_stage = RecruitmentStage.CONTRACT_POOL.value
                contract = await upsert_contract_record_for_progress(
                    progress=progress,
                    job=progress_job,
                    db=session,
                    admin_user_id=admin_user_id,
                    field_updates={
                        "agreement_ref_no": f"{run_tag}-CP-{index:03d}",
                        "rate": Decimal("0.80") + Decimal(index % 5) / Decimal("10"),
                    },
                    data_updates={
                        "contract_review": "待审核" if index % 3 == 0 else "待修改",
                        "signing_status": "已通知人选签合同",
                    },
                )
                contract_number = str(contract.agreement_ref_no or "")
                contract_review = str((contract.data or {}).get("contract_review") or "")
                accepted_rate = str(contract.rate or "")
            else:
                stage_alias = "employed"
                progress.current_stage = RecruitmentStage.ACTIVE.value
                progress.data = {
                    **(progress.data or {}),
                    JobProgressDataKey.ONBOARDING_STATUS.value: "Active",
                }
                contract = await upsert_contract_record_for_progress(
                    progress=progress,
                    job=progress_job,
                    db=session,
                    admin_user_id=admin_user_id,
                    field_updates={
                        "agreement_ref_no": f"{run_tag}-AC-{index:03d}",
                        "rate": Decimal("1.00") + Decimal(index % 4) / Decimal("10"),
                        "contract_status": "Active",
                    },
                    data_updates={
                        "contract_review": "审核通过",
                        "signing_status": "已完成签约",
                    },
                )
                contract_number = str(contract.agreement_ref_no or "")
                contract_review = str((contract.data or {}).get("contract_review") or "")
                accepted_rate = str(contract.rate or "")

            progress.entered_stage_at = submitted_at + timedelta(hours=6)
            await session.commit()

            seeded.append(
                {
                    "index": index,
                    "application_id": int(application.id),
                    "talent_profile_id": int(talent.id),
                    "progress_id": int(progress.id),
                    "full_name": definition["full_name"],
                    "email": definition["email"],
                    "country_of_residence": definition["country_of_residence"],
                    "nationality": definition["nationality"],
                    "additional_languages": definition["additional_languages"],
                    "education_status": definition["education_status"],
                    "expected_salary": definition["expected_salary"],
                    "stage_alias": stage_alias,
                    "resume_uploaded": index % 7 != 0,
                    "source_application_id": int(application.id),
                    "latest_applied_at": submitted_at.isoformat(),
                    "contract_number": contract_number,
                    "contract_review": contract_review,
                    "accepted_rate": accepted_rate,
                    "note": definition["note"],
                }
            )

            if index % 40 == 0 or index == count:
                print_detail(f"seeded progress candidates: {index}/{count}")
    return seeded


async def seed_timesheet_workers_and_records(
    *,
    run_tag: str,
    worker_count: int,
    batch_count: int,
    batch_size: int,
    admin: AdminUser,
    timesheet_job: Job,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records_meta: list[dict[str, Any]] = []
    contracts_by_worker_index: dict[int, Any] = {}
    leader_name_by_user_id: dict[int, str] = {}
    base_now = datetime.now(UTC)

    async with local_session() as session:
        company = await session.get(AdminCompany, int(timesheet_job.company_id))
        if company is None:
            raise RuntimeError("Timesheet company not found.")
        form_template = await ensure_form_template(session)
        await ensure_company_timesheet_config(session, company=company)
        await session.commit()

        for index in range(1, worker_count + 1):
            definition = build_timesheet_worker_definition(run_tag=run_tag, index=index)
            user = await ensure_candidate_user(session, definition)
            applied_at = base_now - timedelta(days=120 + index)
            talent = await ensure_talent_profile(
                session,
                user=user,
                definition=definition,
                job=timesheet_job,
                applied_at=applied_at,
            )
            application = await ensure_application(
                session,
                user=user,
                job=timesheet_job,
                form_template=form_template,
                submitted_at=applied_at,
            )
            talent.source_application_id = int(application.id)
            progress = await ensure_job_progress(
                session,
                user=user,
                application=application,
                talent_profile=talent,
                entered_stage_at=applied_at + timedelta(hours=8),
            )
            contract = await ensure_active_contract_record(
                session,
                admin=admin,
                job=timesheet_job,
                progress=progress,
                definition={
                    **definition,
                    "rate": definition["rate"],
                    "contract_type": "team_leader" if index <= 3 else "normal",
                },
                contract_index=2000 + index,
                effective_date=date.today() - timedelta(days=180 + index),
            )
            contracts_by_worker_index[index] = contract
            leader_name_by_user_id[int(user.id)] = definition["full_name"]

        session_timesheet_job = await session.get(Job, int(timesheet_job.id))
        if session_timesheet_job is None:
            raise RuntimeError("Timesheet job disappeared during seeding.")
        await sync_job_applicant_count(session, job=session_timesheet_job)
        await session.commit()

        batch_templates = [
            {
                "sub_project_name": f"{run_tag} QA Wave Alpha",
                "language": "ja-JP",
                "work_type": "QA",
                "role_name": "Reviewer",
                "project_link": f"https://example.com/{make_slug(run_tag)}/qa-alpha",
                "customer_human_efficiency_minutes": Decimal("18"),
                "candidate_human_efficiency_minutes": Decimal("18"),
                "team_leader_index": 1,
                "non_operational_duration_hours": Decimal("0.50"),
                "extra_notes": "qa alpha batch",
                "poc_evaluation": "stable",
            },
            {
                "sub_project_name": f"{run_tag} Localization Sprint",
                "language": "en-US",
                "work_type": "Annotation",
                "role_name": "Annotator",
                "project_link": f"https://example.com/{make_slug(run_tag)}/loc-sprint",
                "customer_human_efficiency_minutes": Decimal("16"),
                "candidate_human_efficiency_minutes": Decimal("16"),
                "team_leader_index": 2,
                "non_operational_duration_hours": Decimal("0.25"),
                "extra_notes": "loc sprint",
                "poc_evaluation": "fast",
            },
            {
                "sub_project_name": f"{run_tag} Archive Review",
                "language": "fr-FR",
                "work_type": "Review",
                "role_name": "Reviewer",
                "project_link": f"https://example.com/{make_slug(run_tag)}/archive-review",
                "customer_human_efficiency_minutes": Decimal("20"),
                "candidate_human_efficiency_minutes": Decimal("20"),
                "team_leader_index": 3,
                "non_operational_duration_hours": Decimal("1.50"),
                "extra_notes": "archive backlog",
                "poc_evaluation": "careful",
            },
            {
                "sub_project_name": f"{run_tag} QA Wave Beta",
                "language": "ja-JP",
                "work_type": "QA",
                "role_name": "Reviewer",
                "project_link": f"https://example.com/{make_slug(run_tag)}/qa-beta",
                "customer_human_efficiency_minutes": Decimal("18"),
                "candidate_human_efficiency_minutes": Decimal("18"),
                "team_leader_index": 1,
                "non_operational_duration_hours": Decimal("0.75"),
                "extra_notes": "qa beta batch",
                "poc_evaluation": "steady",
            },
            {
                "sub_project_name": f"{run_tag} Training Burst",
                "language": "es-MX",
                "work_type": "Training",
                "role_name": "Trainer",
                "project_link": f"https://example.com/{make_slug(run_tag)}/training-burst",
                "customer_human_efficiency_minutes": Decimal("24"),
                "candidate_human_efficiency_minutes": Decimal("24"),
                "team_leader_index": 2,
                "non_operational_duration_hours": Decimal("0.00"),
                "extra_notes": "training wave",
                "poc_evaluation": "solid",
            },
            {
                "sub_project_name": f"{run_tag} Arabic Coverage",
                "language": "ar-EG",
                "work_type": "Non-Operational",
                "role_name": "QA Specialist",
                "project_link": f"https://example.com/{make_slug(run_tag)}/arabic-coverage",
                "customer_human_efficiency_minutes": Decimal("15"),
                "candidate_human_efficiency_minutes": Decimal("15"),
                "team_leader_index": 3,
                "non_operational_duration_hours": Decimal("2.00"),
                "extra_notes": "coverage support",
                "poc_evaluation": "needs monitor",
            },
        ]

        for batch_index in range(batch_count):
            template = batch_templates[batch_index % len(batch_templates)]
            leader_contract = contracts_by_worker_index[int(template["team_leader_index"])]
            entries: list[ProjectTimesheetBatchCreateEntry] = []
            work_date = date.today() - timedelta(days=batch_index * 5)
            for entry_index in range(batch_size):
                worker_index = ((batch_index * 11 + entry_index) % worker_count) + 1
                contract = contracts_by_worker_index[worker_index]
                output_quantity = Decimal(10 + ((batch_index + entry_index) % 5))
                customer_duration_hours = (
                    template["customer_human_efficiency_minutes"] * output_quantity / Decimal("60")
                ).quantize(Decimal("0.01"))
                candidate_duration_hours = max(
                    Decimal("0.50"),
                    (customer_duration_hours - Decimal("0.50") + Decimal(entry_index % 3) / Decimal("10")).quantize(
                        Decimal("0.01")
                    ),
                )
                entry = ProjectTimesheetBatchCreateEntry(
                    work_date=work_date,
                    contract_record_id=int(contract.id),
                    user_id=int(contract.user_id),
                    work_type=str(template["work_type"]),
                    output_quantity=output_quantity,
                    customer_duration_hours=customer_duration_hours,
                    candidate_duration_hours=candidate_duration_hours,
                    role_name=str(template["role_name"]),
                    non_operational_duration_hours=Decimal(template["non_operational_duration_hours"]),
                    note_asset_ids=[],
                    extra_notes=str(template["extra_notes"]),
                    poc_evaluation=str(template["poc_evaluation"]),
                )
                entries.append(entry)
                records_meta.append(
                    {
                        "sub_project_name": str(template["sub_project_name"]),
                        "work_date": work_date.isoformat(),
                        "user_id": int(contract.user_id),
                        "user_name": contract.contractor_name or "",
                        "team_leader_user_id": int(leader_contract.user_id),
                        "team_leader_name": leader_name_by_user_id[int(leader_contract.user_id)],
                        "language": str(template["language"]),
                        "work_type": str(template["work_type"]),
                        "output_quantity": float(output_quantity),
                        "customer_human_efficiency_minutes": float(template["customer_human_efficiency_minutes"]),
                        "candidate_human_efficiency_minutes": float(template["candidate_human_efficiency_minutes"]),
                        "customer_duration_hours": float(customer_duration_hours),
                        "candidate_duration_hours": float(candidate_duration_hours),
                        "role_name": str(template["role_name"]),
                        "non_operational_duration_hours": float(template["non_operational_duration_hours"]),
                        "project_link": str(template["project_link"]),
                        "poc_evaluation": str(template["poc_evaluation"]),
                        "extra_notes": str(template["extra_notes"]),
                    }
                )

            payload = ProjectTimesheetBatchCreateRequest(
                sub_project_name=str(template["sub_project_name"]),
                language=str(template["language"]),
                project_link=str(template["project_link"]),
                customer_human_efficiency_minutes=Decimal(template["customer_human_efficiency_minutes"]),
                candidate_human_efficiency_minutes=Decimal(template["candidate_human_efficiency_minutes"]),
                team_leader_user_id=int(leader_contract.user_id),
                project_manager_admin_user_id=int(admin.id),
                entries=entries,
            )
            await create_project_timesheet_records(
                company_id=int(timesheet_job.company_id),
                project_id=int(timesheet_job.project_id),
                payload=payload,
                db=session,
                admin_user_id=int(admin.id),
            )
            if (batch_index + 1) % 6 == 0 or batch_index + 1 == batch_count:
                print_detail(f"seeded timesheet batches: {batch_index + 1}/{batch_count}")

        await session.commit()

    return {
        "company_id": int(timesheet_job.company_id),
        "project_id": int(timesheet_job.project_id),
        "leader_name": next(iter(leader_name_by_user_id.values())),
    }, records_meta


def build_progress_query_screening(*, run_tag: str) -> dict[str, Any]:
    return {
        "combinator": "and",
        "rules": [
            {"field": CandidateFieldKey.FULL_NAME.value, "operator": "contains", "value": run_tag},
            {"field": CandidateFieldKey.COUNTRY_OF_RESIDENCE.value, "operator": "=", "value": "Brazil"},
            {"field": CandidateFieldKey.ADDITIONAL_LANGUAGES.value, "operator": "contains", "value": "English"},
            {"field": CandidateFieldKey.EDUCATION_STATUS.value, "operator": "=", "value": "bachelor_completed"},
            {"field": "current_stage", "operator": "=", "value": "screening"},
        ],
    }


def build_progress_query_contract(*, run_tag: str) -> dict[str, Any]:
    return {
        "combinator": "and",
        "rules": [
            {"field": CandidateFieldKey.FULL_NAME.value, "operator": "contains", "value": run_tag},
            {"field": CandidateFieldKey.COUNTRY_OF_RESIDENCE.value, "operator": "=", "value": "Japan"},
            {"field": "current_stage", "operator": "=", "value": "contract"},
            {"field": "contract_review", "operator": "=", "value": "待审核"},
            {"field": "accepted_rate", "operator": ">=", "value": "0.9"},
        ],
    }


def build_candidate_query(*, run_tag: str) -> dict[str, Any]:
    return {
        "combinator": "and",
        "rules": [
            {"field": "nationality", "operator": "contains", "value": "Brazil"},
            {"field": "education", "operator": "=", "value": "bachelor_completed"},
            {"field": "resume_attachment", "operator": "uploaded", "value": ""},
            {"field": "source_application_id", "operator": ">", "value": 50},
            {"field": "latest_applied_job_title", "operator": "contains", "value": run_tag},
        ],
    }


def build_timesheet_query(*, leader_name: str, run_tag: str) -> dict[str, Any]:
    return {
        "combinator": "and",
        "rules": [
            {"field": "sub_project_name", "operator": "contains", "value": f"{run_tag} QA Wave"},
            {"field": "language", "operator": "=", "value": "ja-JP"},
            {"field": "work_type", "operator": "=", "value": "QA"},
            {"field": "role_name", "operator": "=", "value": "Reviewer"},
            {"field": "team_leader_name", "operator": "=", "value": leader_name},
            {"field": "customer_duration_hours", "operator": ">=", "value": "3"},
        ],
    }


def expected_progress_screening_count(rows: list[dict[str, Any]], *, run_tag: str) -> int:
    return len(
        [
            row
            for row in rows
            if run_tag in row["full_name"]
            and row["stage_alias"] == "screening"
            and row["country_of_residence"] == "Brazil"
            and "English" in row["additional_languages"]
            and row["education_status"] == "bachelor_completed"
        ]
    )


def expected_progress_contract_count(rows: list[dict[str, Any]], *, run_tag: str) -> int:
    return len(
        [
            row
            for row in rows
            if run_tag in row["full_name"]
            and row["stage_alias"] == "contract"
            and row["country_of_residence"] == "Japan"
            and row["contract_review"] == "待审核"
            and row["accepted_rate"]
            and Decimal(str(row["accepted_rate"])) >= Decimal("0.9")
        ]
    )


def expected_candidate_count(rows: list[dict[str, Any]], *, run_tag: str) -> int:
    return len(
        [
            row
            for row in rows
            if run_tag in row["full_name"]
            and "Brazil" in row["nationality"]
            and row["education_status"] == "bachelor_completed"
            and row["resume_uploaded"]
            and row["source_application_id"] > 50
        ]
    )


def expected_timesheet_rows(
    rows: list[dict[str, Any]],
    *,
    run_tag: str,
    leader_name: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if start_date <= date.fromisoformat(row["work_date"]) <= end_date
        and row["sub_project_name"].find(f"{run_tag} QA Wave") >= 0
        and row["language"] == "ja-JP"
        and row["work_type"] == "QA"
        and row["role_name"] == "Reviewer"
        and row["team_leader_name"] == leader_name
        and Decimal(str(row["customer_duration_hours"])) >= Decimal("3")
    ]


def build_timesheet_dashboard_expected(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {
            "customer_duration_hours": Decimal("0.00"),
            "candidate_duration_hours": Decimal("0.00"),
            "total_duration_hours": Decimal("0.00"),
        }
    )
    for row in rows:
        language = str(row["language"])
        customer = Decimal(str(row["customer_duration_hours"]))
        candidate = Decimal(str(row["candidate_duration_hours"]))
        grouped[language]["customer_duration_hours"] += customer
        grouped[language]["candidate_duration_hours"] += candidate
        grouped[language]["total_duration_hours"] += customer + candidate
    return {
        language: {key: str(value.quantize(Decimal("0.01"))) for key, value in values.items()}
        for language, values in grouped.items()
    }


async def main() -> None:
    args = parse_args()
    run_tag = make_run_tag()
    report_dir = Path("/Users/ruanhaokang/workspace/hr/hr-server/tmp")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        Path(args.report_path) if args.report_path else report_dir / f"advanced-filter-bulk-{make_slug(run_tag)}.json"
    )

    print_step("Prepare shared admin + form data")
    async with local_session() as session:
        for definition in DICTIONARY_DEFINITIONS:
            await ensure_dictionary(session, definition)
        await session.commit()
        form_template = await ensure_form_template(session)
        await session.commit()

    admin = await ensure_superadmin_user()
    print_detail(f"superadmin ready: username={admin.username} email={admin.email}")

    print_step("Create isolated progress and timesheet jobs")
    async with local_session() as session:
        form_template = await ensure_form_template(session)
        await session.commit()
        progress_job = await ensure_job(
            session,
            owner_admin_user_id=int(admin.id),
            form_template=form_template,
            definition={
                "title": f"{run_tag} Progress Advanced Filter Job",
                "company_name": f"{run_tag} Progress Company",
                "project_name": f"{run_tag} Progress Project",
                "country": "Brazil",
                "work_mode": "Remote",
                "description": f"<p>{run_tag} progress advanced filter bulk verification.</p>",
                "compensation_min": Decimal("8.00"),
                "compensation_max": Decimal("14.00"),
                "compensation_unit": "Per Hour",
                "contract_example": build_contract_example_html(
                    job_title=f"{run_tag} Progress Advanced Filter Job",
                    company_name=f"{run_tag} Progress Company",
                    compensation_unit="Per Hour",
                ),
            },
        )
        timesheet_job = await ensure_job(
            session,
            owner_admin_user_id=int(admin.id),
            form_template=form_template,
            definition={
                "title": f"{run_tag} Timesheet Advanced Filter Job",
                "company_name": f"{run_tag} Timesheet Company",
                "project_name": f"{run_tag} Timesheet Project",
                "country": "Japan",
                "work_mode": "Remote",
                "description": f"<p>{run_tag} timesheet advanced filter bulk verification.</p>",
                "compensation_min": Decimal("10.00"),
                "compensation_max": Decimal("16.00"),
                "compensation_unit": "Per Hour",
                "contract_example": build_contract_example_html(
                    job_title=f"{run_tag} Timesheet Advanced Filter Job",
                    company_name=f"{run_tag} Timesheet Company",
                    compensation_unit="Per Hour",
                ),
            },
        )
        progress_job.status = JobStatus.OPEN.value
        timesheet_job.status = JobStatus.OPEN.value
        await session.commit()
        await session.refresh(progress_job)
        await session.refresh(timesheet_job)
        print_detail(f"progress job id={progress_job.id}")
        print_detail(f"timesheet job id={timesheet_job.id}")

    print_step("Seed progress + talent records")
    progress_rows = await seed_progress_candidates(
        run_tag=run_tag,
        count=int(args.progress_count),
        admin_user_id=int(admin.id),
        progress_job=progress_job,
    )
    print_detail(f"seeded progress/talent rows: {len(progress_rows)}")

    print_step("Seed active contracts + timesheet records")
    workspace_info, timesheet_rows = await seed_timesheet_workers_and_records(
        run_tag=run_tag,
        worker_count=int(args.timesheet_workers),
        batch_count=int(args.timesheet_batches),
        batch_size=int(args.timesheet_batch_size),
        admin=admin,
        timesheet_job=timesheet_job,
    )
    print_detail(f"seeded timesheet rows: {len(timesheet_rows)}")

    print_step("Verify advanced filters through admin APIs")
    transport = ASGITransport(app=admin_app)
    async with httpx.AsyncClient(transport=transport, base_url=args.base_url.rstrip("/"), timeout=120.0) as client:
        login_payload = await login_admin(
            client,
            username_or_email=str(admin.username),
            password="12345678",
        )
        access_token = str(login_payload["access_token"])
        headers = {"Authorization": f"Bearer {access_token}"}

        progress_screening_query = build_progress_query_screening(run_tag=run_tag)
        progress_contract_query = build_progress_query_contract(run_tag=run_tag)
        candidate_query = build_candidate_query(run_tag=run_tag)
        leader_name = str(workspace_info["leader_name"])
        timesheet_query = build_timesheet_query(run_tag=run_tag, leader_name=leader_name)
        timesheet_start_date = date.today() - timedelta(days=70)
        timesheet_end_date = date.today()

        progress_screening_response = ensure_ok(
            await client.get(
                f"/jobs/{int(progress_job.id)}/progress",
                headers=headers,
                params={
                    "active_stage": "screening",
                    "advanced_filter": json.dumps(progress_screening_query, ensure_ascii=False),
                },
            ),
            "Progress screening filter request failed",
        )
        progress_contract_response = ensure_ok(
            await client.get(
                f"/jobs/{int(progress_job.id)}/progress",
                headers=headers,
                params={
                    "active_stage": "contract",
                    "advanced_filter": json.dumps(progress_contract_query, ensure_ascii=False),
                },
            ),
            "Progress contract filter request failed",
        )
        candidate_response = ensure_ok(
            await client.get(
                "/talents",
                headers=headers,
                params={
                    "page": 1,
                    "page_size": 100,
                    "keyword": run_tag,
                    "company_id": int(progress_job.company_id),
                    "project_id": int(progress_job.project_id),
                    "advanced_filter": json.dumps(candidate_query, ensure_ascii=False),
                },
            ),
            "Talent advanced filter request failed",
        )
        timesheet_response = ensure_ok(
            await client.get(
                f"/timesheets/companies/{int(workspace_info['company_id'])}/projects/{int(workspace_info['project_id'])}/workspace",
                headers=headers,
                params={
                    "start_date": timesheet_start_date.isoformat(),
                    "end_date": timesheet_end_date.isoformat(),
                    "advanced_filter": json.dumps(timesheet_query, ensure_ascii=False),
                },
            ),
            "Timesheet advanced filter request failed",
        )

    expected_progress_screening = expected_progress_screening_count(progress_rows, run_tag=run_tag)
    actual_progress_screening = len(progress_screening_response.get("matched_progress_ids") or [])
    expected_progress_contract = expected_progress_contract_count(progress_rows, run_tag=run_tag)
    actual_progress_contract = len(progress_contract_response.get("matched_progress_ids") or [])
    expected_candidates = expected_candidate_count(progress_rows, run_tag=run_tag)
    actual_candidates = int(candidate_response.get("total") or 0)
    expected_timesheet_filtered_rows = expected_timesheet_rows(
        timesheet_rows,
        run_tag=run_tag,
        leader_name=leader_name,
        start_date=timesheet_start_date,
        end_date=timesheet_end_date,
    )
    actual_timesheet_rows = timesheet_response.get("records") or []
    expected_dashboard = build_timesheet_dashboard_expected(expected_timesheet_filtered_rows)
    actual_dashboard = {
        item["language"]: {
            "customer_duration_hours": str(item["customer_duration_hours"]),
            "candidate_duration_hours": str(item["candidate_duration_hours"]),
            "total_duration_hours": str(item["total_duration_hours"]),
        }
        for item in (timesheet_response.get("dashboard_items") or [])
    }

    if actual_progress_screening != expected_progress_screening:
        raise RuntimeError(
            "Progress screening advanced filter mismatch: "
            f"expected {expected_progress_screening}, got {actual_progress_screening}"
        )
    if actual_progress_contract != expected_progress_contract:
        raise RuntimeError(
            "Progress contract advanced filter mismatch: "
            f"expected {expected_progress_contract}, got {actual_progress_contract}"
        )
    if actual_candidates != expected_candidates:
        raise RuntimeError(f"Talent advanced filter mismatch: expected {expected_candidates}, got {actual_candidates}")
    if len(actual_timesheet_rows) != len(expected_timesheet_filtered_rows):
        raise RuntimeError(
            "Timesheet advanced filter mismatch: "
            f"expected {len(expected_timesheet_filtered_rows)}, got {len(actual_timesheet_rows)}"
        )
    if actual_dashboard != expected_dashboard:
        raise RuntimeError(f"Timesheet dashboard mismatch: expected {expected_dashboard}, got {actual_dashboard}")

    report = {
        "run_tag": run_tag,
        "admin": {
            "username": str(admin.username),
            "email": str(admin.email),
        },
        "progress_job": {
            "id": int(progress_job.id),
            "title": str(progress_job.title),
            "company_id": int(progress_job.company_id),
            "project_id": int(progress_job.project_id),
            "seeded_rows": len(progress_rows),
        },
        "timesheet_workspace": {
            "company_id": int(workspace_info["company_id"]),
            "project_id": int(workspace_info["project_id"]),
            "seeded_rows": len(timesheet_rows),
        },
        "checks": [
            {
                "module": "progress",
                "name": "screening_complex",
                "expected": expected_progress_screening,
                "actual": actual_progress_screening,
            },
            {
                "module": "progress",
                "name": "contract_complex",
                "expected": expected_progress_contract,
                "actual": actual_progress_contract,
            },
            {
                "module": "candidate_pool",
                "name": "talent_complex",
                "expected": expected_candidates,
                "actual": actual_candidates,
            },
            {
                "module": "timesheet_workspace",
                "name": "timesheet_complex",
                "expected": len(expected_timesheet_filtered_rows),
                "actual": len(actual_timesheet_rows),
            },
        ],
        "timesheet_dashboard": {
            "expected": expected_dashboard,
            "actual": actual_dashboard,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print_step("Verification complete")
    print_detail(f"run_tag={run_tag}")
    print_detail(
        f"progress seeded={len(progress_rows)} "
        f"screening_match={actual_progress_screening} contract_match={actual_progress_contract}"
    )
    print_detail(f"candidate total match={actual_candidates}")
    print_detail(f"timesheet seeded={len(timesheet_rows)} filtered_match={len(actual_timesheet_rows)}")
    print_detail(f"report saved to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
