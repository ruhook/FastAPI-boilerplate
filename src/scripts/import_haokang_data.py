import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import func, select

try:
    import openpyxl
except ModuleNotFoundError:
    bundled_site_packages = (
        "/Users/ruanhaokang/.cache/codex-runtimes/codex-primary-runtime/"
        "dependencies/python/lib/python3.12/site-packages"
    )
    if bundled_site_packages not in sys.path:
        sys.path.append(bundled_site_packages)
    import openpyxl

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

DEFAULT_XLSX_PATH = "/Users/ruanhaokang/Downloads/浩康导入数据2.0.xlsx"
DEFAULT_CANDIDATE_PASSWORD = "Candidate123!"
IMPORT_ADMIN_EMAIL = "haokang-import-admin@example.com"
IMPORT_ADMIN_USERNAME = "haokangimport"
IMPORT_ADMIN_PASSWORD = "HaokangImport123!"
COMPANY_NAME = "字节"
PROJECT_NAME = "PH-DA"
FALLBACK_IMPORT_DATE = date(2026, 5, 24)
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
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
    phone: str | None
    team_leader_label: str | None
    referrer: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class TimesheetRow:
    source: str
    row_number: int
    sub_project_name: str
    work_date: date
    name: str
    email: str | None
    language: str
    work_type: str
    output_quantity: Decimal | None
    customer_human_efficiency_minutes: Decimal | None
    candidate_human_efficiency_minutes: Decimal | None
    customer_duration_hours: Decimal | None
    candidate_duration_hours: Decimal | None
    role_name: str | None
    non_operational_duration_hours: Decimal | None
    project_link: str | None
    poc_evaluation: str | None
    extra_notes: str | None
    team_leader_label: str | None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?"} else text


def optional_text(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def extract_emails(value: Any) -> list[str]:
    text = "" if value is None else str(value)
    return [item.lower() for item in EMAIL_PATTERN.findall(text)]


def primary_email(value: Any) -> tuple[str | None, list[str]]:
    emails = extract_emails(value)
    if not emails:
        return None, []
    return emails[0], emails[1:]


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and value > 20000:
        return date.fromordinal(date(1899, 12, 30).toordinal() + int(value))
    text = clean_text(value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


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
    text = value.strip()
    return text[:limit] if len(text) > limit else text


def rows_by_header(ws, *, header_row: int, max_col: int | None = None) -> list[tuple[int, dict[str, Any]]]:
    rows = list(ws.iter_rows(min_row=header_row, max_col=max_col, values_only=True))
    if not rows:
        return []
    headers = [clean_text(value) or f"col{index + 1}" for index, value in enumerate(rows[0])]
    output: list[tuple[int, dict[str, Any]]] = []
    for offset, values in enumerate(rows[1:], start=header_row + 1):
        item = {headers[index]: values[index] if index < len(values) else None for index in range(len(headers))}
        if any(clean_text(value) for value in item.values()):
            output.append((offset, item))
    return output


def parse_candidates(wb: Any) -> tuple[list[CandidateRow], list[str]]:
    candidates: list[CandidateRow] = []
    anomalies: list[str] = []
    seen_emails: set[str] = set()
    for row_number, row in rows_by_header(wb["name list（Copy）"], header_row=1, max_col=16):
        name = clean_text(row.get("Name"))
        if not name or name == "Name":
            continue
        email, extra_emails = primary_email(row.get("Email"))
        if not email:
            anomalies.append(f"candidate row {row_number}: skipped `{name}` because email is blank/invalid")
            continue
        if email in seen_emails:
            anomalies.append(f"candidate row {row_number}: skipped duplicate email `{email}` for `{name}`")
            continue
        seen_emails.add(email)
        if extra_emails:
            anomalies.append(
                f"candidate row {row_number}: `{name}` has extra email(s) {', '.join(extra_emails)}; using {email}"
            )
        candidates.append(
            CandidateRow(
                row_number=row_number,
                name=name,
                email=email,
                extra_emails=extra_emails,
                ref_no=optional_text(row.get("Ref.NO.")),
                country=clean_text(row.get("country")) or "UNKNOWN",
                language=clean_text(row.get("语种")) or clean_text(row.get("country")) or "UNKNOWN",
                status=clean_text(row.get("状态")) or "未知",
                rate=to_decimal(row.get("价格（USD）")),
                education=optional_text(row.get("学历")),
                phone=optional_text(row.get("手机号")),
                team_leader_label=optional_text(row.get("组长")),
                referrer=optional_text(row.get("推荐人")),
                raw=row,
            )
        )
    return candidates, anomalies


def parse_onboarding_dates(wb: Any) -> dict[str, date]:
    dates: dict[str, date] = {}
    for _, row in rows_by_header(wb["🤡 新人转化情况"], header_row=1, max_col=16):
        email, _ = primary_email(row.get("Email"))
        onboarded_at = to_date(row.get("入职日期\n（飞书加好友）"))
        if email and onboarded_at:
            dates[email] = onboarded_at
    return dates


def timesheet_key(item: TimesheetRow) -> tuple[str, str, str, str]:
    return (
        item.name.lower().strip(),
        item.work_date.isoformat(),
        str(item.output_quantity or ""),
        str(item.customer_duration_hours or ""),
    )


def parse_b_timesheets(wb: Any) -> tuple[list[TimesheetRow], list[str]]:
    items: list[TimesheetRow] = []
    anomalies: list[str] = []
    for row_number, row in rows_by_header(wb["B端菲律宾工时统计"], header_row=3, max_col=17):
        name = clean_text(row.get("人名"))
        work_date = to_date(row.get("日期"))
        sub_project_name = clean_text(row.get("子项目名称"))
        if not name or not work_date or not sub_project_name:
            anomalies.append(f"B timesheet row {row_number}: skipped because name/date/sub-project is incomplete")
            continue
        email, _ = primary_email(row.get("邮箱"))
        customer_duration = to_decimal(row.get("时长（客户）"))
        candidate_duration = to_decimal(row.get("时长（人选）")) or customer_duration
        items.append(
            TimesheetRow(
                source="B端菲律宾工时统计",
                row_number=row_number,
                sub_project_name=sub_project_name,
                work_date=work_date,
                name=name,
                email=email,
                language=clean_text(row.get("语种")) or "UNKNOWN",
                work_type=clean_text(row.get("工时类型")) or "生产工时",
                output_quantity=to_decimal(row.get("产量")),
                customer_human_efficiency_minutes=None,
                candidate_human_efficiency_minutes=to_decimal(row.get("人效（人选）")),
                customer_duration_hours=customer_duration,
                candidate_duration_hours=candidate_duration,
                role_name=optional_text(row.get("角色")),
                non_operational_duration_hours=to_decimal(row.get("非作业时长")),
                project_link=optional_text(row.get("项目链接")),
                poc_evaluation=optional_text(row.get("评价")),
                extra_notes=optional_text(row.get("备注-图片")),
                team_leader_label=optional_text(row.get("负责组长")),
            )
        )
    return items, anomalies


def parse_overview_timesheets(
    wb: Any,
    *,
    b_keys: set[tuple[str, str, str, str]],
) -> tuple[list[TimesheetRow], list[str], int]:
    items: list[TimesheetRow] = []
    anomalies: list[str] = []
    skipped_duplicate = 0
    for row_number, row in rows_by_header(wb["总览（客户视角）"], header_row=1, max_col=17):
        name = clean_text(row.get("人名"))
        work_date = to_date(row.get("日期"))
        sub_project_name = clean_text(row.get("项目名称"))
        if not name or not work_date or not sub_project_name:
            continue
        email, _ = primary_email(row.get("邮箱"))
        item = TimesheetRow(
            source="总览（客户视角）",
            row_number=row_number,
            sub_project_name=sub_project_name,
            work_date=work_date,
            name=name,
            email=email,
            language=clean_text(row.get("语种")) or "UNKNOWN",
            work_type=clean_text(row.get("工时类型")) or "生产工时",
            output_quantity=to_decimal(row.get("产量")),
            customer_human_efficiency_minutes=None,
            candidate_human_efficiency_minutes=None,
            customer_duration_hours=to_decimal(row.get("作业时长 h")),
            candidate_duration_hours=to_decimal(row.get("作业时长 h")),
            role_name=optional_text(row.get("角色")),
            non_operational_duration_hours=to_decimal(row.get("非作业时长")),
            project_link=optional_text(row.get("项目链接")),
            poc_evaluation=None,
            extra_notes=optional_text(row.get("备注")),
            team_leader_label=None,
        )
        if item.language == "en-RoW (PH)" and timesheet_key(item) in b_keys:
            skipped_duplicate += 1
            continue
        items.append(item)
    return items, anomalies, skipped_duplicate


def parse_team_leaders(wb: Any) -> dict[str, Decimal]:
    leaders: dict[str, Decimal] = {}
    for _, row in rows_by_header(wb["组长工资"], header_row=1, max_col=4):
        name = clean_text(row.get("组长姓名"))
        base_pay = to_decimal(row.get("base pay"))
        if name and base_pay is not None:
            leaders[name] = base_pay
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


async def ensure_import_admin(session) -> AdminUser:
    result = await session.execute(select(AdminUser).where(AdminUser.email == IMPORT_ADMIN_EMAIL))
    admin = result.scalar_one_or_none()
    if admin is None:
        admin = AdminUser(
            name="Haokang Import",
            username=IMPORT_ADMIN_USERNAME,
            email=IMPORT_ADMIN_EMAIL,
            hashed_password=get_password_hash(IMPORT_ADMIN_PASSWORD),
            phone=None,
            note="Local data import admin.",
            status="enabled",
            profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
            is_superuser=True,
            role_id=None,
            data={},
        )
        session.add(admin)
        await session.flush()
    return admin


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
        data={"placeholder": True, "import_source": "浩康导入数据2.0.xlsx"},
    )
    return int(asset.id)


async def ensure_company_and_project(
    session,
    *,
    languages: list[str],
    work_types: list[str],
    roles: list[str],
) -> tuple[AdminCompany, AdminCompanyProject]:
    result = await session.execute(
        select(AdminCompany).where(
            AdminCompany.name == COMPANY_NAME,
            AdminCompany.is_deleted.is_(False),
        )
    )
    company = result.scalar_one_or_none()
    data = {
        COMPANY_DATA_TIMESHEET_LANGUAGES_KEY: languages,
        COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY: work_types,
        COMPANY_DATA_TIMESHEET_ROLES_KEY: roles,
    }
    if company is None:
        company = AdminCompany(
            name=COMPANY_NAME,
            description="Imported from 浩康导入数据2.0.xlsx.",
            logo_asset_id=None,
            data=data,
        )
        session.add(company)
        await session.flush()
    else:
        company.data = {**(company.data or {}), **data}

    result = await session.execute(
        select(AdminCompanyProject).where(
            AdminCompanyProject.company_id == company.id,
            AdminCompanyProject.name == PROJECT_NAME,
            AdminCompanyProject.is_deleted.is_(False),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        project = AdminCompanyProject(company_id=company.id, name=PROJECT_NAME, data={"source": "浩康导入数据2.0.xlsx"})
        session.add(project)
        await session.flush()
    return company, project


async def ensure_jobs(
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
    active_by_country: dict[str, list[CandidateRow]] = {}
    for candidate in candidates:
        active_by_country.setdefault(candidate.country, []).append(candidate)

    for country, rows in sorted(active_by_country.items()):
        rates = [candidate.rate for candidate in rows if candidate.rate is not None]
        title = f"{COMPANY_NAME} {country} 数据标注岗位"
        result = await session.execute(
            select(Job).where(Job.title == title, Job.company_id == company.id, Job.is_deleted.is_(False))
        )
        job = result.scalar_one_or_none()
        data = {
            JOB_DATA_FORM_FIELDS_KEY: form_fields,
            JOB_DATA_CONTRACT_EXAMPLE_KEY: build_contract_example_html(
                job_title=title,
                company_name=company.name,
                compensation_unit="Per Hour",
            ),
            "import_source": "浩康导入数据2.0.xlsx",
        }
        if job is None:
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
                description=f"<p>{COMPANY_NAME} {PROJECT_NAME} imported contractor role for {country}.</p>",
                applicant_count=0,
                owner_admin_user_id=admin.id,
                form_template_id=form_template_id,
                assessment_enabled=False,
                data=data,
            )
            session.add(job)
            await session.flush()
        jobs[country] = job

    leader_title = f"{COMPANY_NAME} 组长岗位"
    result = await session.execute(
        select(Job).where(Job.title == leader_title, Job.company_id == company.id, Job.is_deleted.is_(False))
    )
    leader_job = result.scalar_one_or_none()
    leader_values = list(leader_rates.values())
    if leader_job is None:
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
            description=f"<p>{COMPANY_NAME} {PROJECT_NAME} imported team leader role.</p>",
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
                "import_source": "浩康导入数据2.0.xlsx",
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
    if key in leader_aliases:
        return leader_aliases[key]
    return None


async def count_tables(session) -> dict[str, int]:
    tables = {
        "admin_company": AdminCompany,
        "admin_company_project": AdminCompanyProject,
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


async def import_data(args: argparse.Namespace) -> dict[str, Any]:
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    candidates, candidate_anomalies = parse_candidates(wb)
    onboarding_dates = parse_onboarding_dates(wb)
    leader_rates = parse_team_leaders(wb)
    b_timesheets, b_anomalies = parse_b_timesheets(wb)
    overview_timesheets, overview_anomalies, skipped_overview_duplicates = parse_overview_timesheets(
        wb,
        b_keys={timesheet_key(item) for item in b_timesheets},
    )
    timesheets = overview_timesheets + b_timesheets

    by_email = {candidate.email: candidate for candidate in candidates}
    by_name = {candidate.name.lower().strip(): candidate for candidate in candidates}
    active_candidates = [candidate for candidate in candidates if candidate.status == "在职"]
    matched_timesheets: list[tuple[TimesheetRow, CandidateRow]] = []
    skipped_timesheets: list[str] = []
    seen_timesheet_keys: set[tuple[Any, ...]] = set()

    for item in timesheets:
        candidate = resolve_candidate(item, by_email=by_email, by_name=by_name)
        if candidate is None:
            skipped_timesheets.append(f"{item.source} row {item.row_number}: no candidate match for `{item.name}`")
            continue
        dedupe_key = (
            candidate.email,
            item.sub_project_name,
            item.work_date.isoformat(),
            item.work_type,
            item.role_name or "",
            str(item.output_quantity or ""),
            str(item.customer_duration_hours or ""),
            item.source,
            item.row_number,
        )
        if dedupe_key in seen_timesheet_keys:
            continue
        seen_timesheet_keys.add(dedupe_key)
        matched_timesheets.append((item, candidate))

    first_work_date_by_email: dict[str, date] = {}
    latest_work_date: date | None = None
    last_work_date_by_email: dict[str, date] = {}
    for item, candidate in matched_timesheets:
        latest_work_date = item.work_date if latest_work_date is None else max(latest_work_date, item.work_date)
        previous = first_work_date_by_email.get(candidate.email)
        if previous is None or item.work_date < previous:
            first_work_date_by_email[candidate.email] = item.work_date
        last_previous = last_work_date_by_email.get(candidate.email)
        if last_previous is None or item.work_date > last_previous:
            last_work_date_by_email[candidate.email] = item.work_date
    latest_effective_date = latest_work_date or FALLBACK_IMPORT_DATE
    timesheet_candidate_emails = {candidate.email for _, candidate in matched_timesheets}
    contract_candidates = [
        candidate
        for candidate in candidates
        if candidate.status == "在职" or candidate.email in timesheet_candidate_emails
    ]

    summary: dict[str, Any] = {
        "source_xlsx": args.xlsx,
        "apply": args.apply,
        "candidates": len(candidates),
        "active_candidates": len(active_candidates),
        "contract_candidates": len(contract_candidates),
        "candidate_anomalies": candidate_anomalies[:20],
        "onboarding_dates": len(onboarding_dates),
        "team_leaders": len(leader_rates),
        "timesheets_parsed": len(timesheets),
        "timesheets_matched": len(matched_timesheets),
        "timesheets_skipped": len(skipped_timesheets),
        "timesheet_skip_examples": skipped_timesheets[:20],
        "skipped_overview_ph_duplicates": skipped_overview_duplicates,
        "parse_anomalies": (b_anomalies + overview_anomalies)[:20],
        "latest_work_date": latest_effective_date.isoformat(),
    }

    if not args.apply:
        return summary

    async with local_session() as session:
        admin = await ensure_import_admin(session)
        for definition in DICTIONARY_DEFINITIONS:
            await ensure_dictionary(session, definition)
        form_template = await ensure_form_template(session)
        referral_bonus_model = await ensure_referral_bonus_model(session)
        placeholder_contract_asset_id = await create_import_contract_placeholder_asset(session, admin=admin)
        company, project = await ensure_company_and_project(
            session,
            languages=sorted({item.language for item, _ in matched_timesheets if item.language}),
            work_types=sorted({item.work_type for item, _ in matched_timesheets if item.work_type}),
            roles=sorted({item.role_name for item, _ in matched_timesheets if item.role_name}),
        )
        jobs_by_country, leader_job = await ensure_jobs(
            session,
            admin=admin,
            company=company,
            project=project,
            form_template_id=form_template.id,
            form_fields=form_template.fields,
            referral_bonus_model_id=referral_bonus_model.id,
            candidates=candidates,
            leader_rates=leader_rates,
        )

        username_used: set[str] = set()
        users_by_email: dict[str, User] = {}
        profiles_by_email: dict[str, TalentProfile] = {}
        candidate_password_hash = get_password_hash(DEFAULT_CANDIDATE_PASSWORD)
        for candidate in candidates:
            username = make_username(candidate.email, username_used)
            user = User(
                name=truncated(candidate.name, 30),
                username=username,
                email=candidate.email,
                hashed_password=candidate_password_hash,
                profile_image_url=DEFAULT_USER_PROFILE_IMAGE_URL,
                data={
                    "import_source": "浩康导入数据2.0.xlsx",
                    "source_row": candidate.row_number,
                    "full_name": candidate.name,
                    "extra_emails": candidate.extra_emails,
                    "status": candidate.status,
                },
            )
            session.add(user)
            users_by_email[candidate.email] = user
        await session.flush()

        for candidate in candidates:
            user = users_by_email[candidate.email]
            profile = TalentProfile(
                user_id=user.id,
                full_name=candidate.name,
                email=candidate.email,
                whatsapp=candidate.phone,
                nationality=candidate.country,
                location=candidate.country,
                education=candidate.education,
                resume_asset_id=None,
                latest_applied_job_id=None,
                latest_applied_job_title=None,
                latest_applied_at=None,
                note=None,
                source_application_id=None,
                merge_strategy="haokang_import",
                last_merged_at=datetime.now(UTC),
                data={
                    "import_source": "浩康导入数据2.0.xlsx",
                    "ref_no": candidate.ref_no,
                    "language": candidate.language,
                    "rate": str(candidate.rate) if candidate.rate is not None else None,
                    "status": candidate.status,
                    "team_leader_label": candidate.team_leader_label,
                    "referrer": candidate.referrer,
                },
            )
            session.add(profile)
            profiles_by_email[candidate.email] = profile
        await session.flush()

        progress_by_email: dict[str, JobProgress] = {}
        normal_contract_by_email: dict[str, ContractRecord] = {}
        active_referral_profile_targets: list[tuple[int, Job, ContractRecord]] = []
        application_count_by_job_id: dict[int, int] = {}
        for candidate in contract_candidates:
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            job = jobs_by_country[candidate.country]
            is_active_candidate = candidate.status == "在职"
            current_stage = RecruitmentStage.ACTIVE.value if is_active_candidate else RecruitmentStage.REPLACED.value
            contract_status = CONTRACT_STATUS_ACTIVE if is_active_candidate else CONTRACT_STATUS_TERMINATED
            effective_date = (
                onboarding_dates.get(candidate.email)
                or first_work_date_by_email.get(candidate.email)
                or latest_effective_date
            )
            end_date = (
                None
                if is_active_candidate
                else last_work_date_by_email.get(candidate.email, latest_effective_date)
            )
            application = CandidateApplication(
                user_id=user.id,
                job_id=job.id,
                form_template_id=form_template.id,
                job_snapshot_title=job.title,
                status=CandidateApplicationStatus.SUBMITTED.value,
                submitted_at=as_utc_datetime(effective_date),
                data={"import_source": "浩康导入数据2.0.xlsx", "source_row": candidate.row_number},
            )
            session.add(application)
            await session.flush()
            values = [
                field_value(application.id, CandidateFieldKey.FULL_NAME, candidate.name, 1),
                field_value(application.id, CandidateFieldKey.EMAIL, candidate.email, 2),
                field_value(application.id, CandidateFieldKey.COUNTRY_OF_RESIDENCE, candidate.country, 3),
                field_value(application.id, CandidateFieldKey.NATIVE_LANGUAGES, candidate.language, 4),
                field_value(application.id, CandidateFieldKey.EDUCATION_STATUS, candidate.education, 5),
                field_value(
                    application.id,
                    CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR,
                    str(candidate.rate) if candidate.rate is not None else None,
                    6,
                ),
                field_value(application.id, CandidateFieldKey.WHATSAPP, candidate.phone, 7),
            ]
            session.add_all(values)
            progress = JobProgress(
                job_id=job.id,
                user_id=user.id,
                application_id=application.id,
                talent_profile_id=profile.id,
                current_stage=current_stage,
                screening_mode=RecruitmentScreeningMode.MANUAL.value,
                assessment_reviewer_admin_user_id=None,
                assessment_assigned_at=None,
                entered_stage_at=as_utc_datetime(effective_date),
                data={
                    "onboarding_status": "imported_active" if is_active_candidate else "imported_historical_inactive",
                    "accepted_rate": str(candidate.rate) if candidate.rate is not None else None,
                    "contract_number": candidate.ref_no,
                    "import_source": "浩康导入数据2.0.xlsx",
                    "source_status": candidate.status,
                },
            )
            session.add(progress)
            await session.flush()
            profile.latest_applied_job_id = job.id
            profile.latest_applied_job_title = job.title
            profile.latest_applied_at = application.submitted_at
            profile.source_application_id = application.id
            progress_by_email[candidate.email] = progress
            application_count_by_job_id[job.id] = application_count_by_job_id.get(job.id, 0) + 1

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
                agreement_ref_no=candidate.ref_no,
                contract_status=contract_status,
                contract_type=CONTRACT_TYPE_NORMAL,
                contractor_name=candidate.name,
                rate=candidate.rate,
                legal_entity="T-Maxx International",
                worker_type="Contractor",
                effective_date=effective_date,
                end_date=end_date,
                draft_contract_asset_id=None,
                candidate_signed_contract_asset_id=None,
                company_sealed_contract_asset_id=(
                    placeholder_contract_asset_id if contract_status == CONTRACT_STATUS_ACTIVE else None
                ),
                contract_attachment_asset_id=(
                    placeholder_contract_asset_id if contract_status == CONTRACT_STATUS_ACTIVE else None
                ),
                parse_status="imported",
                parse_error=None,
                version=1,
                is_current=True,
                created_by_admin_user_id=admin.id,
                updated_by_admin_user_id=admin.id,
                data={
                    "import_source": "浩康导入数据2.0.xlsx",
                    "contract_rule": "normal_active" if is_active_candidate else "normal_historical_terminated",
                    "source_status": candidate.status,
                    "contract_placeholder_asset_id": (
                        placeholder_contract_asset_id if contract_status == CONTRACT_STATUS_ACTIVE else None
                    ),
                },
            )
            session.add(contract)
            normal_contract_by_email[candidate.email] = contract
            if contract_status == CONTRACT_STATUS_ACTIVE:
                active_referral_profile_targets.append((int(user.id), job, contract))
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
                summary.setdefault("leader_skips", []).append(f"leader `{leader_name}` not found in candidate roster")
                continue
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            leader_effective_date = latest_effective_date
            application = CandidateApplication(
                user_id=user.id,
                job_id=leader_job.id,
                form_template_id=form_template.id,
                job_snapshot_title=leader_job.title,
                status=CandidateApplicationStatus.SUBMITTED.value,
                submitted_at=as_utc_datetime(leader_effective_date),
                data={"import_source": "浩康导入数据2.0.xlsx", "team_leader_contract": True},
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
                entered_stage_at=as_utc_datetime(leader_effective_date),
                data={"onboarding_status": "imported_team_leader", "import_source": "浩康导入数据2.0.xlsx"},
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
                effective_date=leader_effective_date,
                end_date=None,
                company_sealed_contract_asset_id=placeholder_contract_asset_id,
                contract_attachment_asset_id=placeholder_contract_asset_id,
                parse_status="imported",
                version=1,
                is_current=True,
                created_by_admin_user_id=admin.id,
                updated_by_admin_user_id=admin.id,
                data={
                    "import_source": "浩康导入数据2.0.xlsx",
                    "contract_rule": "team_leader_latest_only",
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
            first_token = leader_name.split()[0].lower()
            leader_aliases.setdefault(first_token, user.id)
        if leader_contracts:
            application_count_by_job_id[leader_job.id] = (
                application_count_by_job_id.get(leader_job.id, 0) + leader_contracts
            )
        await session.flush()

        timesheet_records = 0
        for item, candidate in matched_timesheets:
            user = users_by_email[candidate.email]
            profile = profiles_by_email[candidate.email]
            contract = normal_contract_by_email.get(candidate.email)
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
                team_leader_user_id=resolve_leader_user_id(item.team_leader_label, leader_aliases),
                language=item.language,
                work_type=item.work_type,
                output_quantity=item.output_quantity,
                customer_human_efficiency_minutes=item.customer_human_efficiency_minutes,
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
                data={"import_source": item.source, "source_row": item.row_number},
            )
            session.add(record)
            timesheet_records += 1

        for job_id, count in application_count_by_job_id.items():
            job = await session.get(Job, job_id)
            if job is not None:
                job.applicant_count = count

        await session.commit()
        summary["leader_contracts_created"] = leader_contracts
        summary["timesheet_records_created"] = timesheet_records
        summary["table_counts"] = await count_tables(session)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Haokang Excel data into the local HR database.")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX_PATH, help="Path to 浩康导入数据2.0.xlsx.")
    parser.add_argument("--apply", action="store_true", help="Write records to the database. Omit for dry-run.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        summary = await import_data(args)
        for key, value in summary.items():
            print(f"{key}: {value}")
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
