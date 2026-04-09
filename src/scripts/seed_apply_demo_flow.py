import asyncio
import json
import logging
from decimal import Decimal

from sqlalchemy import or_, select

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.dictionary.model import AdminDictionary
from ..app.modules.admin.form_template.model import AdminFormTemplate
from ..app.modules.job.const import JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..app.modules.job.model import Job
from ..app.modules.admin.mail_account.model import MailAccount  # noqa: F401
from ..app.modules.admin.mail_signature.model import MailSignature  # noqa: F401
from ..app.modules.admin.mail_template.model import MailTemplate  # noqa: F401
from ..app.modules.admin.role.model import Role
from .seed_candidate_base_form_template import (
    DICTIONARY_DEFINITIONS,
    FIELD_DESCRIPTIONS,
    TEMPLATE_NAME,
    FORM_TEMPLATE_FIELDS,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEMO_ROLE_NAME = "报名流程测试管理员"
DEMO_ADMIN_NAME = "Apply Flow Admin"
DEMO_ADMIN_USERNAME = "flowadmin"
DEMO_ADMIN_EMAIL = "flow-admin@example.com"
DEMO_ADMIN_PASSWORD = "FlowAdmin123!"
DEMO_ROLE_PERMISSIONS = ["岗位管理", "总人才库", "常量字典", "报名表单策略", "邮件与模板"]

DEMO_JOBS = [
    {
        "title": "Portuguese Data Annotator Demo",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<h3>Portuguese Data Annotator Demo</h3><p>This job is prepared for end-to-end apply flow testing.</p>",
        "compensation_min": Decimal("2.00"),
        "compensation_max": Decimal("5.00"),
        "compensation_unit": "Per Hour",
    },
    {
        "title": "Brazil Language QA Reviewer Demo",
        "country": "Brazil",
        "work_mode": "Remote",
        "description": "<h3>Brazil Language QA Reviewer Demo</h3><p>This second job is prepared for the manual merge verification step.</p>",
        "compensation_min": Decimal("6.00"),
        "compensation_max": Decimal("10.00"),
        "compensation_unit": "Per Hour",
    },
]


async def ensure_dictionary(session, definition: dict) -> AdminDictionary:
    key = definition.get("key")
    label = definition["label"]
    result = await session.execute(
        select(AdminDictionary).where(
            or_(
                AdminDictionary.key == key,
                AdminDictionary.label == label,
            )
        )
    )
    dictionary = result.scalar_one_or_none()
    if dictionary is None:
        dictionary = AdminDictionary(
            key=key,
            label=label,
            options=definition["options"],
            data={},
        )
        session.add(dictionary)
    else:
        dictionary.key = key
        dictionary.label = label
        dictionary.options = definition["options"]
        dictionary.is_deleted = False
        dictionary.deleted_at = None
    await session.flush()
    await session.refresh(dictionary)
    return dictionary


async def ensure_form_template(session) -> AdminFormTemplate:
    result = await session.execute(
        select(AdminFormTemplate).where(AdminFormTemplate.name == TEMPLATE_NAME)
    )
    template = result.scalar_one_or_none()
    data = {
        "field_descriptions": FIELD_DESCRIPTIONS,
    }
    if template is None:
        template = AdminFormTemplate(
            name=TEMPLATE_NAME,
            description="基础候选人报名模板，用于联调报名流程。",
            fields=FORM_TEMPLATE_FIELDS,
            data=data,
        )
        session.add(template)
    else:
        template.description = "基础候选人报名模板，用于联调报名流程。"
        template.fields = FORM_TEMPLATE_FIELDS
        template.data = data
        template.is_deleted = False
        template.deleted_at = None
    await session.flush()
    await session.refresh(template)
    return template


async def ensure_role(session) -> Role:
    result = await session.execute(select(Role).where(Role.name == DEMO_ROLE_NAME))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(
            name=DEMO_ROLE_NAME,
            description="用于岗位创建与报名流程联调。",
            enabled=True,
            permissions=DEMO_ROLE_PERMISSIONS,
            data={},
        )
        session.add(role)
    else:
        role.description = "用于岗位创建与报名流程联调。"
        role.enabled = True
        role.permissions = DEMO_ROLE_PERMISSIONS
    await session.flush()
    await session.refresh(role)
    return role


async def ensure_admin_user(session, *, role_id: int) -> AdminUser:
    result = await session.execute(
        select(AdminUser).where(
            or_(
                AdminUser.username == DEMO_ADMIN_USERNAME,
                AdminUser.email == DEMO_ADMIN_EMAIL,
            )
        )
    )
    admin = result.scalar_one_or_none()
    hashed_password = get_password_hash(DEMO_ADMIN_PASSWORD)
    if admin is None:
        admin = AdminUser(
            name=DEMO_ADMIN_NAME,
            username=DEMO_ADMIN_USERNAME,
            email=DEMO_ADMIN_EMAIL,
            hashed_password=hashed_password,
            phone=None,
            note="Seeded admin for apply flow demo.",
            status="enabled",
            profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
            is_superuser=False,
            role_id=role_id,
            data={},
        )
        session.add(admin)
    else:
        admin.name = DEMO_ADMIN_NAME
        admin.username = DEMO_ADMIN_USERNAME
        admin.email = DEMO_ADMIN_EMAIL
        admin.hashed_password = hashed_password
        admin.note = "Seeded admin for apply flow demo."
        admin.status = "enabled"
        admin.profile_image_url = DEFAULT_ADMIN_PROFILE_IMAGE_URL
        admin.is_superuser = False
        admin.role_id = role_id
        admin.is_deleted = False
        admin.deleted_at = None
    await session.flush()
    await session.refresh(admin)
    return admin


async def ensure_job(
    session,
    *,
    owner_admin_user_id: int,
    form_template: AdminFormTemplate,
    definition: dict,
) -> Job:
    result = await session.execute(
        select(Job).where(
            Job.title == definition["title"],
            Job.owner_admin_user_id == owner_admin_user_id,
            Job.is_deleted.is_(False),
        )
    )
    job = result.scalar_one_or_none()
    data = {JOB_DATA_FORM_FIELDS_KEY: form_template.fields}
    if job is None:
        job = Job(
            title=definition["title"],
            company_name="DA",
            country=definition["country"],
            status=JobStatus.OPEN.value,
            work_mode=definition["work_mode"],
            compensation_min=definition["compensation_min"],
            compensation_max=definition["compensation_max"],
            compensation_unit=definition["compensation_unit"],
            description=definition["description"],
            applicant_count=0,
            owner_admin_user_id=owner_admin_user_id,
            form_template_id=form_template.id,
            assessment_enabled=False,
            data=data,
        )
        session.add(job)
    else:
        job.company_name = "DA"
        job.country = definition["country"]
        job.status = JobStatus.OPEN.value
        job.work_mode = definition["work_mode"]
        job.compensation_min = definition["compensation_min"]
        job.compensation_max = definition["compensation_max"]
        job.compensation_unit = definition["compensation_unit"]
        job.description = definition["description"]
        job.form_template_id = form_template.id
        job.assessment_enabled = False
        job.data = data
        job.is_deleted = False
        job.deleted_at = None
    await session.flush()
    await session.refresh(job)
    return job


async def main() -> None:
    try:
        async with local_session() as session:
            for definition in DICTIONARY_DEFINITIONS:
                await ensure_dictionary(session, definition)

            form_template = await ensure_form_template(session)
            role = await ensure_role(session)
            admin = await ensure_admin_user(session, role_id=role.id)

            jobs: list[Job] = []
            for definition in DEMO_JOBS:
                jobs.append(
                    await ensure_job(
                        session,
                        owner_admin_user_id=admin.id,
                        form_template=form_template,
                        definition=definition,
                    )
                )

            await session.commit()

            payload = {
                "admin": {
                    "username": DEMO_ADMIN_USERNAME,
                    "email": DEMO_ADMIN_EMAIL,
                    "password": DEMO_ADMIN_PASSWORD,
                    "role": DEMO_ROLE_NAME,
                },
                "form_template": {
                    "id": form_template.id,
                    "name": form_template.name,
                },
                "jobs": [
                    {
                        "id": job.id,
                        "title": job.title,
                        "status": job.status,
                    }
                    for job in jobs
                ],
            }
            logger.info("Apply flow demo seed prepared successfully.")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
