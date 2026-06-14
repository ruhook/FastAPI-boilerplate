import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import func, select

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from ..app.modules.admin.company.service import (
    COMPANY_DATA_TIMESHEET_LANGUAGES_KEY,
    COMPANY_DATA_TIMESHEET_ROLES_KEY,
    COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY,
)
from ..app.modules.assets.schema import AssetUploadPayload
from ..app.modules.assets.service import create_asset_from_bytes
from ..app.modules.candidate_application.const import CandidateApplicationStatus
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.candidate_application_field_value.model import CandidateApplicationFieldValue
from ..app.modules.candidate_field.const import CANDIDATE_FIELD_CN_NAME_MAP, CandidateFieldKey
from ..app.modules.contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_TERMINATED,
    CONTRACT_TYPE_NORMAL,
    CONTRACT_TYPE_TEAM_LEADER,
)
from ..app.modules.contract_record.model import ContractRecord
from ..app.modules.job.const import JOB_DATA_CONTRACT_EXAMPLE_KEY, JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..app.modules.job.model import Job
from ..app.modules.job_progress.const import RecruitmentScreeningMode, RecruitmentStage
from ..app.modules.job_progress.model import JobProgress
from ..app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from ..app.modules.referral_bonus_model.const import (
    DEFAULT_REFERRAL_BONUS_CAP,
    DEFAULT_REFERRAL_BONUS_MODEL_NAME,
    default_referral_bonus_milestones_payload,
)
from ..app.modules.referral_bonus_model.model import ReferralBonusModel
from ..app.modules.referral_bonus_model.service import ensure_user_referral_profile_from_job
from ..app.modules.talent_profile.model import TalentProfile
from ..app.modules.user.const import DEFAULT_USER_PROFILE_IMAGE_URL
from ..app.modules.user.model import User
from .seed_apply_demo_flow import build_contract_example_html, ensure_dictionary, ensure_form_template
from .seed_candidate_base_form_template import DICTIONARY_DEFINITIONS

DEFAULT_PAYLOAD_PATH = "/data/test/py-apps/hr-server/tmp/haokang_import/haokang_visible_import_payload.json"
DEFAULT_PASSWORD = "12345678"
COMPANY_NAME = "字节"
PROJECT_NAME = "PH-DA"
ACTIVE_STATUSES = {"在职", "休假"}
PLACEHOLDER_CONTRACT_FILENAME = "haokang-import-contract-placeholder.docx"
PLACEHOLDER_CONTRACT_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(slots=True)
class CandidateRow:
    row_number: int
    name: str
    email: str
    extra_emails: list[str]
    ref_no: str | None
    country: str
    language: str
    status: str
    rate: Decimal | None
    education: str | None
    team_leader_label: str | None
    referrer: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class TimesheetRow:
    row_number: int
    sub_project_name: str
    work_date: date
    name: str
    email: str | None
    language: str
    work_type: str
    output_quantity: Decimal | None
    customer_duration_hours: Decimal | None
    candidate_duration_hours: Decimal | None
    candidate_human_efficiency_minutes: Decimal | None
    role_name: str | None
    non_operational_duration_hours: Decimal | None
    project_link: str | None
    poc_evaluation: str | None
    extra_notes: str | None
    team_leader_label: str | None
    raw: dict[str, Any]


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def as_utc_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def build_blank_docx_bytes() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p/></w:body></w:document>",
        )
    return buffer.getvalue()


def truncated(value: str, limit: int) -> str:
    return value.strip()[:limit]


def load_payload(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_candidates(payload: dict[str, Any]) -> list[CandidateRow]:
    candidates: list[CandidateRow] = []
    for raw in payload["candidates"]:
        candidates.append(
            CandidateRow(
                row_number=int(raw["row_number"]),
                name=str(raw["name"]).strip(),
                email=str(raw["email"]).strip().lower(),
                extra_emails=list(raw.get("extra_emails") or []),
                ref_no=optional_text(raw.get("ref_no")),
                country=str(raw.get("country") or "UNKNOWN").strip() or "UNKNOWN",
                language=str(raw.get("language") or raw.get("country") or "UNKNOWN").strip() or "UNKNOWN",
                status=str(raw.get("status") or "未知").strip(),
                rate=to_decimal(raw.get("rate")),
                education=optional_text(raw.get("education")),
                team_leader_label=optional_text(raw.get("team_leader_label")),
                referrer=optional_text(raw.get("referrer")),
                raw=raw,
            )
        )
    return candidates


def parse_timesheets(payload: dict[str, Any]) -> list[TimesheetRow]:
    items: list[TimesheetRow] = []
    for raw in payload["timesheets"]:
        items.append(
            TimesheetRow(
                row_number=int(raw["row_number"]),
                sub_project_name=str(raw["sub_project_name"]).strip(),
                work_date=to_date(raw["work_date"]),
                name=str(raw["name"]).strip(),
                email=optional_text(raw.get("email")),
                language=str(raw.get("language") or "UNKNOWN").strip() or "UNKNOWN",
                work_type=str(raw.get("work_type") or "生产工时").strip() or "生产工时",
                output_quantity=to_decimal(raw.get("output_quantity")),
                customer_duration_hours=to_decimal(raw.get("customer_duration_hours")),
                candidate_duration_hours=to_decimal(raw.get("candidate_duration_hours")),
                candidate_human_efficiency_minutes=to_decimal(raw.get("candidate_human_efficiency_minutes")),
                role_name=optional_text(raw.get("role_name")),
                non_operational_duration_hours=to_decimal(raw.get("non_operational_duration_hours")),
                project_link=optional_text(raw.get("project_link")),
                poc_evaluation=optional_text(raw.get("poc_evaluation")),
                extra_notes=optional_text(raw.get("extra_notes")),
                team_leader_label=optional_text(raw.get("team_leader_label")),
                raw=raw,
            )
        )
    return items


def parse_team_leaders(payload: dict[str, Any]) -> dict[str, Decimal]:
    leaders: dict[str, Decimal] = {}
    for raw in payload["team_leaders"]:
        rate = to_decimal(raw.get("base_pay"))
        name = str(raw.get("name") or "").strip()
        if name and rate is not None:
            leaders[name] = rate
    return leaders


def make_username(email: str, used: set[str]) -> str:
    local = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9]", "", local) or "candidate"
    base = base[:16]
    candidate = base[:20]
    suffix = 1
    while candidate in used:
        tail = str(suffix)
        candidate = f"{base[: 20 - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def field_value(
    application_id: int,
    field_key: CandidateFieldKey,
    value: str | None,
    sort_order: int,
) -> CandidateApplicationFieldValue:
    label = CANDIDATE_FIELD_CN_NAME_MAP[field_key]
    return CandidateApplicationFieldValue(
        application_id=application_id,
        field_key=field_key.value,
        field_label=label,
        field_type="text",
        catalog_key=None,
        raw_value=value,
        display_value=value,
        asset_id=None,
        sort_order=sort_order,
    )


async def ensure_admin_accounts(session, payload: dict[str, Any], password_hash: str) -> list[AdminUser]:
    admins: list[AdminUser] = []
    for item in payload["admin_accounts"]:
        result = await session.execute(select(AdminUser).where(AdminUser.username == item["username"]))
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = AdminUser(
                name=truncated(item["name"], 30),
                username=truncated(item["username"], 20),
                email=truncated(item["email"], 100),
                hashed_password=password_hash,
                phone=None,
                note="Haokang visible-sheet import admin.",
                status="enabled",
                profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
                is_superuser=True,
                role_id=None,
                data={"import_source": "haokang_visible_payload"},
            )
            session.add(admin)
        else:
            admin.name = truncated(item["name"], 30)
            admin.email = truncated(item["email"], 100)
            admin.hashed_password = password_hash
            admin.status = "enabled"
            admin.is_superuser = True
            admin.is_deleted = False
            admin.deleted_at = None
        admins.append(admin)
    await session.flush()
    return admins


async def ensure_referral_bonus_model(session) -> ReferralBonusModel:
    result = await session.execute(
        select(ReferralBonusModel).where(
            ReferralBonusModel.name == DEFAULT_REFERRAL_BONUS_MODEL_NAME,
            ReferralBonusModel.is_deleted.is_(False),
        )
    )
    model = result.scalar_one_or_none()
    if model is None:
        model = ReferralBonusModel(
            name=DEFAULT_REFERRAL_BONUS_MODEL_NAME,
            status="active",
            currency="USD",
            reward_cap=DEFAULT_REFERRAL_BONUS_CAP,
            data={"milestones": default_referral_bonus_milestones_payload()},
        )
        session.add(model)
        await session.flush()
    return model


async def create_import_contract_placeholder_asset(session, *, admin: AdminUser) -> int:
    asset = await create_asset_from_bytes(
        db=session,
        payload=AssetUploadPayload(
            type="contract_attachment",
            module="contract",
            owner_type="admin_user",
            owner_id=int(admin.id),
        ),
        original_name=PLACEHOLDER_CONTRACT_FILENAME,
        content=build_blank_docx_bytes(),
        mime_type=PLACEHOLDER_CONTRACT_MIME_TYPE,
        data={"placeholder": True, "import_source": "haokang_visible_payload"},
    )
    return int(asset.id)


async def ensure_company_and_project(
    session,
    *,
    company_name: str,
    project_name: str,
    languages: list[str],
    work_types: list[str],
    roles: list[str],
) -> tuple[AdminCompany, AdminCompanyProject]:
    data = {
        COMPANY_DATA_TIMESHEET_LANGUAGES_KEY: languages,
        COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY: work_types,
        COMPANY_DATA_TIMESHEET_ROLES_KEY: roles,
    }
    company = AdminCompany(
        name=company_name,
        description="Imported from Haokang visible sheets.",
        logo_asset_id=None,
        data=data,
    )
    session.add(company)
    await session.flush()
    project = AdminCompanyProject(
        company_id=company.id,
        name=project_name,
        data={"source": "haokang_visible_payload"},
    )
    session.add(project)
    await session.flush()
    return company, project


async def create_jobs(
    session,
    *,
    admin: AdminUser,
    company: AdminCompany,
    project: AdminCompanyProject,
    form_template_id: int,
    form_fields: list[dict[str, Any]],
    referral_bonus_model_id: int,
    candidates: list[CandidateRow],
    leader_rates: dict[str, Decimal],
) -> tuple[dict[str, Job], Job]:
    jobs: dict[str, Job] = {}
    by_country: dict[str, list[CandidateRow]] = {}
    for candidate in candidates:
        by_country.setdefault(candidate.country, []).append(candidate)
    for country, rows in sorted(by_country.items()):
        rates = [candidate.rate for candidate in rows if candidate.rate is not None]
        title = f"{company.name} {country} 数据标注岗位"
        job = Job(
            title=title,
            company_id=company.id,
            project_id=project.id,
            referral_bonus_model_id=referral_bonus_model_id,
            country=country,
            status=JobStatus.OPEN.value,
            work_mode="Remote",
            compensation_min=min(rates) if rates else None,
            compensation_max=max(rates) if rates else None,
            compensation_unit="Per Hour",
            description=f"<p>{company.name} {project.name} visible-sheet imported contractor role for {country}.</p>",
            applicant_count=0,
            owner_admin_user_id=admin.id,
            form_template_id=form_template_id,
            assessment_enabled=False,
            data={
                JOB_DATA_FORM_FIELDS_KEY: form_fields,
                JOB_DATA_CONTRACT_EXAMPLE_KEY: build_contract_example_html(
                    job_title=title,
                    company_name=company.name,
                    compensation_unit="Per Hour",
                ),
                "import_source": "haokang_visible_payload",
            },
        )
        session.add(job)
        await session.flush()
        jobs[country] = job

    leader_title = f"{company.name} 组长岗位"
    leader_values = list(leader_rates.values())
    leader_job = Job(
        title=leader_title,
        company_id=company.id,
        project_id=project.id,
        referral_bonus_model_id=referral_bonus_model_id,
        country="GLOBAL",
        status=JobStatus.OPEN.value,
        work_mode="Remote",
        compensation_min=min(leader_values) if leader_values else None,
        compensation_max=max(leader_values) if leader_values else None,
        compensation_unit="Per Month",
        description=f"<p>{company.name} {project.name} visible-sheet imported team leader role.</p>",
        applicant_count=0,
        owner_admin_user_id=admin.id,
        form_template_id=form_template_id,
        assessment_enabled=False,
        data={
            JOB_DATA_FORM_FIELDS_KEY: form_fields,
            JOB_DATA_CONTRACT_EXAMPLE_KEY: build_contract_example_html(
                job_title=leader_title,
                company_name=company.name,
                compensation_unit="Per Month",
            ),
            "import_source": "haokang_visible_payload",
        },
    )
    session.add(leader_job)
    await session.flush()
    return jobs, leader_job


def resolve_candidate(
    item: TimesheetRow,
    *,
    by_email: dict[str, CandidateRow],
    by_name: dict[str, CandidateRow],
) -> CandidateRow | None:
    if item.email and item.email in by_email:
        return by_email[item.email]
    return by_name.get(item.name.lower().strip())


def resolve_leader_user_id(label: str | None, leader_aliases: dict[str, int]) -> int | None:
    if not label:
        return None
    key = label.lower().strip()
    return leader_aliases.get(key)


async def count_tables(session) -> dict[str, int]:
    tables = {
        "admin_company": AdminCompany,
        "admin_company_project": AdminCompanyProject,
        "admin_user": AdminUser,
        "job": Job,
        "user": User,
        "talent_profile": TalentProfile,
        "candidate_application": CandidateApplication,
        "job_progress": JobProgress,
        "contract_record": ContractRecord,
        "project_timesheet_record": ProjectTimesheetRecord,
    }
    output: dict[str, int] = {}
    for name, model in tables.items():
        result = await session.execute(select(func.count()).select_from(model))
        output[name] = int(result.scalar_one())
    return output


async def import_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_payload(args.payload)
    candidates = parse_candidates(payload)
    timesheets = parse_timesheets(payload)
    leader_rates = parse_team_leaders(payload)
    password = args.password or payload.get("password") or DEFAULT_PASSWORD
    password_hash = get_password_hash(password)
    company_name = payload.get("company_name") or COMPANY_NAME
    project_name = payload.get("project_name") or PROJECT_NAME
    team_leader_effective_date = to_date(payload.get("team_leader_effective_date") or "2026-05-01")

    by_email = {candidate.email: candidate for candidate in candidates}
    by_name = {candidate.name.lower().strip(): candidate for candidate in candidates}

    matched_timesheets: list[tuple[TimesheetRow, CandidateRow]] = []
    skipped_timesheets: list[str] = []
    seen_timesheet_keys: set[tuple[Any, ...]] = set()
    for item in timesheets:
        candidate = resolve_candidate(item, by_email=by_email, by_name=by_name)
        if candidate is None:
            skipped_timesheets.append(f"B row {item.row_number}: no candidate match for `{item.name}`")
            continue
        dedupe_key = (
            candidate.email,
            item.sub_project_name,
            item.work_date.isoformat(),
            item.work_type,
            item.role_name or "",
            str(item.output_quantity or ""),
            str(item.customer_duration_hours or ""),
            item.row_number,
        )
        if dedupe_key in seen_timesheet_keys:
            continue
        seen_timesheet_keys.add(dedupe_key)
        matched_timesheets.append((item, candidate))

    first_work_date_by_email: dict[str, date] = {}
    last_work_date_by_email: dict[str, date] = {}
    for item, candidate in matched_timesheets:
        previous = first_work_date_by_email.get(candidate.email)
        if previous is None or item.work_date < previous:
            first_work_date_by_email[candidate.email] = item.work_date
        last_previous = last_work_date_by_email.get(candidate.email)
        if last_previous is None or item.work_date > last_previous:
            last_work_date_by_email[candidate.email] = item.work_date

    timesheet_candidate_emails = {candidate.email for _, candidate in matched_timesheets}
    contract_candidates = [
        candidate
        for candidate in candidates
        if candidate.status in ACTIVE_STATUSES or candidate.email in timesheet_candidate_emails
    ]

    languages = sorted({item.language for item in timesheets} | {candidate.language for candidate in candidates})
    work_types = sorted({item.work_type for item in timesheets})
    roles = sorted({item.role_name for item in timesheets if item.role_name})

    summary: dict[str, Any] = {
        "payload": args.payload,
        "visible_sheets": payload.get("visible_sheets", []),
        "candidates_in_payload": len(candidates),
        "timesheets_in_payload": len(timesheets),
        "team_leaders_in_payload": len(leader_rates),
        "payload_anomalies": {
            key: len(value) for key, value in (payload.get("anomalies") or {}).items()
        },
        "skipped_timesheets": skipped_timesheets[:20],
        "skipped_timesheet_count": len(skipped_timesheets),
    }
    if not args.apply:
        summary["dry_run"] = True
        summary["matched_timesheets"] = len(matched_timesheets)
        summary["contract_candidates"] = len(contract_candidates)
        return summary

    async with local_session() as session:
        admins = await ensure_admin_accounts(session, payload, password_hash)
        admin = admins[0]
        for definition in DICTIONARY_DEFINITIONS:
            await ensure_dictionary(session, definition)
        form_template = await ensure_form_template(session)
        referral_bonus_model = await ensure_referral_bonus_model(session)
        placeholder_contract_asset_id = await create_import_contract_placeholder_asset(session, admin=admin)
        company, project = await ensure_company_and_project(
            session,
            company_name=company_name,
            project_name=project_name,
            languages=languages,
            work_types=work_types,
            roles=roles,
        )
        form_fields = list(form_template.fields or [])
        jobs_by_country, leader_job = await create_jobs(
            session,
            admin=admin,
            company=company,
            project=project,
            form_template_id=form_template.id,
            form_fields=form_fields,
            referral_bonus_model_id=referral_bonus_model.id,
            candidates=candidates,
            leader_rates=leader_rates,
        )

        used_usernames: set[str] = set()
        users_by_email: dict[str, User] = {}
        profiles_by_email: dict[str, TalentProfile] = {}
        for candidate in candidates:
            user = User(
                name=truncated(candidate.name, 30),
                username=make_username(candidate.email, used_usernames),
                email=candidate.email,
                hashed_password=password_hash,
                profile_image_url=DEFAULT_USER_PROFILE_IMAGE_URL,
                data={
                    "import_source": "haokang_visible_payload",
                    "source_sheet": "在职名单",
                    "source_row": candidate.row_number,
                    "status": candidate.status,
                    "ref_no": candidate.ref_no,
                    "language": candidate.language,
                    "country": candidate.country,
                    "team_leader_label": candidate.team_leader_label,
                },
            )
            session.add(user)
            await session.flush()
            profile = TalentProfile(
                user_id=user.id,
                full_name=candidate.name,
                email=candidate.email,
                nationality=candidate.country,
                location=candidate.country,
                education=candidate.education,
                note=f"Visible sheet status: {candidate.status}",
            )
            session.add(profile)
            await session.flush()
            users_by_email[candidate.email] = user
            profiles_by_email[candidate.email] = profile

        normal_contract_by_email: dict[str, ContractRecord] = {}
        active_referral_profile_targets: list[tuple[int, Job, ContractRecord]] = []
        application_count_by_job_id: dict[int, int] = {}
        for candidate in contract_candidates:
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            job = jobs_by_country[candidate.country]
            effective_date = first_work_date_by_email.get(candidate.email) or team_leader_effective_date
            is_active = candidate.status in ACTIVE_STATUSES
            stage = RecruitmentStage.ACTIVE.value if is_active else RecruitmentStage.REPLACED.value
            application = CandidateApplication(
                user_id=user.id,
                job_id=job.id,
                form_template_id=form_template.id,
                job_snapshot_title=job.title,
                status=CandidateApplicationStatus.SUBMITTED.value,
                submitted_at=as_utc_datetime(effective_date),
                data={"import_source": "haokang_visible_payload"},
            )
            session.add(application)
            await session.flush()
            for sort_order, field_key, value in [
                (1, CandidateFieldKey.FULL_NAME, candidate.name),
                (2, CandidateFieldKey.EMAIL, candidate.email),
                (3, CandidateFieldKey.COUNTRY_OF_RESIDENCE, candidate.country),
                (4, CandidateFieldKey.EDUCATION_STATUS, candidate.education),
            ]:
                session.add(field_value(application.id, field_key, value, sort_order))
            progress = JobProgress(
                job_id=job.id,
                user_id=user.id,
                application_id=application.id,
                talent_profile_id=profile.id,
                current_stage=stage,
                screening_mode=RecruitmentScreeningMode.MANUAL.value,
                entered_stage_at=as_utc_datetime(effective_date),
                data={"onboarding_status": "imported_visible", "import_source": "haokang_visible_payload"},
            )
            session.add(progress)
            await session.flush()
            contract = ContractRecord(
                user_id=user.id,
                user_snapshot_name=candidate.name,
                user_snapshot_email=candidate.email,
                talent_profile_id=profile.id,
                application_id=application.id,
                job_id=job.id,
                job_progress_id=progress.id,
                job_snapshot_title=job.title,
                previous_contract_record_id=None,
                service_customer_company_id=company.id,
                service_customer_project_id=project.id,
                agreement_ref_no=candidate.ref_no or candidate.email,
                contract_status=CONTRACT_STATUS_ACTIVE if is_active else CONTRACT_STATUS_TERMINATED,
                contract_type=CONTRACT_TYPE_NORMAL,
                contractor_name=candidate.name,
                rate=candidate.rate,
                legal_entity="T-Maxx International",
                worker_type="Contractor",
                effective_date=effective_date,
                end_date=None if is_active else last_work_date_by_email.get(candidate.email),
                company_sealed_contract_asset_id=placeholder_contract_asset_id if is_active else None,
                contract_attachment_asset_id=placeholder_contract_asset_id if is_active else None,
                parse_status="imported",
                version=1,
                is_current=True,
                created_by_admin_user_id=admin.id,
                updated_by_admin_user_id=admin.id,
                data={
                    "import_source": "haokang_visible_payload",
                    "contract_placeholder_asset_id": placeholder_contract_asset_id if is_active else None,
                },
            )
            session.add(contract)
            normal_contract_by_email[candidate.email] = contract
            if is_active:
                active_referral_profile_targets.append((int(user.id), job, contract))
            application_count_by_job_id[job.id] = application_count_by_job_id.get(job.id, 0) + 1
        await session.flush()
        for user_id, job, contract in active_referral_profile_targets:
            await ensure_user_referral_profile_from_job(
                user_id=user_id,
                job=job,
                db=session,
                admin_user_id=int(admin.id),
                contract_record=contract,
            )

        leader_aliases: dict[str, int] = {}
        leader_contracts = 0
        for leader_name, base_pay in leader_rates.items():
            candidate = by_name.get(leader_name.lower().strip())
            if candidate is None or candidate.email not in users_by_email:
                summary.setdefault("leader_skips", []).append(f"leader `{leader_name}` not found in visible roster")
                continue
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            application = CandidateApplication(
                user_id=user.id,
                job_id=leader_job.id,
                form_template_id=form_template.id,
                job_snapshot_title=leader_job.title,
                status=CandidateApplicationStatus.SUBMITTED.value,
                submitted_at=as_utc_datetime(team_leader_effective_date),
                data={"import_source": "haokang_visible_payload", "team_leader_contract": True},
            )
            session.add(application)
            await session.flush()
            progress = JobProgress(
                job_id=leader_job.id,
                user_id=user.id,
                application_id=application.id,
                talent_profile_id=profile.id,
                current_stage=RecruitmentStage.ACTIVE.value,
                screening_mode=RecruitmentScreeningMode.MANUAL.value,
                entered_stage_at=as_utc_datetime(team_leader_effective_date),
                data={"onboarding_status": "imported_team_leader", "import_source": "haokang_visible_payload"},
            )
            session.add(progress)
            await session.flush()
            contract = ContractRecord(
                user_id=user.id,
                user_snapshot_name=candidate.name,
                user_snapshot_email=candidate.email,
                talent_profile_id=profile.id,
                application_id=application.id,
                job_id=leader_job.id,
                job_progress_id=progress.id,
                job_snapshot_title=leader_job.title,
                previous_contract_record_id=None,
                service_customer_company_id=company.id,
                service_customer_project_id=project.id,
                agreement_ref_no=f"{candidate.ref_no or candidate.email}-TL",
                contract_status=CONTRACT_STATUS_ACTIVE,
                contract_type=CONTRACT_TYPE_TEAM_LEADER,
                contractor_name=candidate.name,
                rate=base_pay,
                legal_entity="T-Maxx International",
                worker_type="Team Leader",
                effective_date=team_leader_effective_date,
                end_date=None,
                company_sealed_contract_asset_id=placeholder_contract_asset_id,
                contract_attachment_asset_id=placeholder_contract_asset_id,
                parse_status="imported",
                version=1,
                is_current=True,
                created_by_admin_user_id=admin.id,
                updated_by_admin_user_id=admin.id,
                data={
                    "import_source": "haokang_visible_payload",
                    "contract_rule": "visible_sheet_team_leader_may_effective",
                    "base_pay": str(base_pay),
                    "contract_placeholder_asset_id": placeholder_contract_asset_id,
                },
            )
            session.add(contract)
            await session.flush()
            await ensure_user_referral_profile_from_job(
                user_id=int(user.id),
                job=leader_job,
                db=session,
                admin_user_id=int(admin.id),
                contract_record=contract,
            )
            leader_contracts += 1
            leader_aliases[leader_name.lower()] = user.id
            leader_aliases.setdefault(leader_name.split()[0].lower(), user.id)
        if leader_contracts:
            application_count_by_job_id[leader_job.id] = (
                application_count_by_job_id.get(leader_job.id, 0) + leader_contracts
            )
        await session.flush()

        timesheet_records = 0
        timesheets_with_leader = 0
        for item, candidate in matched_timesheets:
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            contract = normal_contract_by_email.get(candidate.email)
            leader_user_id = resolve_leader_user_id(item.team_leader_label, leader_aliases)
            if leader_user_id is not None:
                timesheets_with_leader += 1
            record = ProjectTimesheetRecord(
                company_id=company.id,
                project_id=project.id,
                sub_project_name=item.sub_project_name,
                work_date=item.work_date,
                user_id=user.id,
                talent_profile_id=profile.id,
                contract_record_id=contract.id if contract is not None else None,
                user_name_snapshot=candidate.name,
                user_email_snapshot=candidate.email,
                team_leader_user_id=leader_user_id,
                language=item.language,
                work_type=item.work_type,
                output_quantity=item.output_quantity,
                customer_human_efficiency_minutes=None,
                candidate_human_efficiency_minutes=item.candidate_human_efficiency_minutes,
                customer_duration_hours=item.customer_duration_hours,
                candidate_duration_hours=item.candidate_duration_hours,
                role_name=item.role_name,
                non_operational_duration_hours=item.non_operational_duration_hours,
                project_link=item.project_link,
                poc_evaluation=item.poc_evaluation,
                extra_notes=item.extra_notes,
                created_by_admin_user_id=admin.id,
                updated_by_admin_user_id=admin.id,
                data={"import_source": "B端菲律宾工时统计", "source_row": item.row_number},
            )
            session.add(record)
            timesheet_records += 1

        for job_id, count in application_count_by_job_id.items():
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one()
            job.applicant_count = count

        await session.commit()
        counts = await count_tables(session)

    summary.update(
        {
            "dry_run": False,
            "admin_accounts": [item["username"] for item in payload["admin_accounts"]],
            "users": len(users_by_email),
            "normal_contracts": len(normal_contract_by_email),
            "team_leader_contracts": leader_contracts,
            "timesheet_records": timesheet_records,
            "timesheets_with_team_leader": timesheets_with_leader,
            "counts": counts,
        }
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Haokang visible-sheet payload into the configured HR database."
    )
    parser.add_argument("--payload", default=DEFAULT_PAYLOAD_PATH)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


async def amain() -> None:
    summary = await import_payload(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
