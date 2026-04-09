from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.security import get_password_hash
from src.app.modules.admin.form_template.model import AdminFormTemplate
from src.app.modules.assets.model import Asset
from src.app.modules.job.const import JOB_DATA_AUTOMATION_RULES_KEY, JOB_DATA_FORM_FIELDS_KEY, JobStatus
from src.app.modules.job.model import Job
from src.app.modules.operation_log.model import OperationLog
from src.app.modules.user.model import User


BASE_FORM_FIELDS: list[dict[str, object]] = [
    {"key": "full_name", "label": "Full Name", "type": "text", "required": True, "canFilter": True},
    {"key": "email", "label": "Email", "type": "text", "required": True, "canFilter": True},
    {"key": "whatsapp", "label": "WhatsApp", "type": "text", "required": True, "canFilter": True},
    {"key": "nationality", "label": "Nationality/Citizenship", "type": "text", "required": False, "canFilter": True},
    {
        "key": "country_of_residence",
        "label": "Country of residence",
        "type": "text",
        "required": True,
        "canFilter": True,
    },
    {
        "key": "education_status",
        "label": "What is your current education status?",
        "type": "single_select",
        "required": True,
        "canFilter": True,
    },
    {
        "key": "resume_attachment",
        "label": "Please upload your most updated comprehensive English Resume here.",
        "type": "attachment",
        "required": True,
        "canFilter": False,
    },
]


def build_form_fields() -> list[dict[str, object]]:
    return deepcopy(BASE_FORM_FIELDS)


async def create_candidate_user(
    db_session: AsyncSession,
    *,
    suffix: str,
    name: str = "Candidate Tester",
    password: str = "CandidatePass123!",
) -> tuple[User, str]:
    username = f"cand{suffix.lower()}"[:20]
    user = User(
        name=name,
        username=username,
        email=f"candidate.{suffix}@example.com",
        hashed_password=get_password_hash(password),
        profile_image_url="https://www.profileimageurl.com",
        data={},
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def login_web_user(
    web_client: AsyncClient,
    *,
    username: str,
    password: str,
) -> dict[str, str]:
    response = await web_client.post(
        "/api/v1/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    access_token = response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def create_form_template(
    db_session: AsyncSession,
    *,
    suffix: str,
    fields: list[dict[str, object]] | None = None,
) -> AdminFormTemplate:
    template = AdminFormTemplate(
        name=f"Candidate Base Template {suffix}",
        description="Test-only candidate application template",
        fields=fields or build_form_fields(),
        data={},
    )
    db_session.add(template)
    await db_session.commit()
    await db_session.refresh(template)
    return template


async def create_resume_asset(
    db_session: AsyncSession,
    *,
    suffix: str,
    original_name: str = "resume.pdf",
) -> Asset:
    asset = Asset(
        type="file",
        module="candidate",
        owner_type="user",
        owner_id=None,
        original_name=original_name,
        storage_key=f"tests/{suffix}/{uuid4().hex}-{original_name}",
        mime_type="application/pdf",
        file_size=128,
        data={},
    )
    db_session.add(asset)
    await db_session.commit()
    await db_session.refresh(asset)
    return asset


async def create_open_job(
    db_session: AsyncSession,
    *,
    suffix: str,
    title: str,
    owner_admin_user_id: int,
    form_template_id: int,
    form_fields: list[dict[str, object]] | None = None,
    company_name: str = "DA",
    assessment_enabled: bool = False,
    automation_rules: dict[str, object] | None = None,
) -> Job:
    job = Job(
        title=title,
        company_name=company_name,
        country="Brazil",
        status=JobStatus.OPEN.value,
        work_mode="Remote",
        compensation_min=Decimal("2.00"),
        compensation_max=Decimal("5.00"),
        compensation_unit="Per Hour",
        description=f"<p>{title} description for test {suffix}</p>",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=form_template_id,
        assessment_enabled=assessment_enabled,
        data={
            JOB_DATA_FORM_FIELDS_KEY: form_fields or build_form_fields(),
            JOB_DATA_AUTOMATION_RULES_KEY: automation_rules or {"combinator": "and", "rules": []},
        },
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def build_application_items(
    *,
    full_name: str,
    email: str,
    whatsapp: str,
    nationality: str,
    country_of_residence: str,
    education_status: str,
    resume_asset_id: int,
) -> list[dict[str, object]]:
    return [
        {"field_key": "full_name", "value": full_name},
        {"field_key": "email", "value": email},
        {"field_key": "whatsapp", "value": whatsapp},
        {"field_key": "nationality", "value": nationality},
        {"field_key": "country_of_residence", "value": country_of_residence},
        {"field_key": "education_status", "value": education_status},
        {
            "field_key": "resume_attachment",
            "value": original_name_from_asset_id(resume_asset_id),
            "display_value": original_name_from_asset_id(resume_asset_id),
            "asset_id": resume_asset_id,
        },
    ]


def build_automation_rules(
    *,
    field_key: str,
    operator: str,
    value: str | int | float | list[str],
    second_value: str | int | float | None = None,
    combinator: str = "and",
) -> dict[str, object]:
    rule: dict[str, object] = {
        "fieldKey": field_key,
        "fieldLabel": field_key,
        "fieldType": "text",
        "operator": operator,
        "value": value,
    }
    if second_value is not None:
        rule["secondValue"] = second_value
    return {"combinator": combinator, "rules": [rule]}


def original_name_from_asset_id(asset_id: int) -> str:
    return f"resume-{asset_id}.pdf"


async def fetch_operation_logs(
    db_session: AsyncSession,
    *,
    user_id: int,
) -> list[OperationLog]:
    result = await db_session.execute(
        select(OperationLog).where(OperationLog.user_id == user_id).order_by(OperationLog.id.asc())
    )
    return list(result.scalars().all())
