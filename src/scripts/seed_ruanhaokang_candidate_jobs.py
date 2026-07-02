import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.form_template.model import AdminFormTemplate
from ..app.modules.assets.model import Asset
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.candidate_application_field_value.model import CandidateApplicationFieldValue
from ..app.modules.candidate_field.const import CandidateFieldKey
from ..app.modules.job.const import (
    JOB_DATA_APPLICATION_SUMMARY_KEY,
    JOB_DATA_AUTOMATION_RULES_KEY,
    JOB_DATA_CONTRACT_EXAMPLE_KEY,
    JOB_DATA_FORM_FIELDS_KEY,
    JOB_DATA_LANGUAGES_KEY,
    JOB_DATA_SHOW_COMPENSATION_KEY,
    JobStatus,
)
from ..app.modules.job.model import Job
from ..app.modules.job_progress.const import JobProgressDataKey, RecruitmentScreeningMode, RecruitmentStage
from ..app.modules.job_progress.model import JobProgress
from ..app.modules.talent_profile.model import TalentProfile
from ..app.modules.user.const import DEFAULT_USER_PROFILE_IMAGE_URL
from ..app.modules.user.model import User
from .seed_apply_demo_flow import (
    ensure_admin_user,
    ensure_company,
    ensure_company_project,
    ensure_referral_bonus_model,
    ensure_role,
)
from .seed_candidate_base_form_template import TEMPLATE_NAME
from .seed_candidate_base_form_template import seed as seed_candidate_base_form_template

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_KEY = "ruanhaokang_candidate_stage_seed_v1"
SEED_COMPANY_NAME = "Haokang Local Seed"
SEED_PROJECT_NAME = "Candidate Stage Demo"

USERNAME = "ruanhaokang"
PASSWORD = "12345678"
EMAIL = "ruanhaokang@example.com"
DISPLAY_NAME = "Ruan Haokang"

RESUME_STORAGE_KEY = "seed/ruanhaokang-candidate-stage-demo/resume.pdf"

STAGE_CASES: list[dict[str, Any]] = [
    {
        "stage": RecruitmentStage.PENDING_SCREENING,
        "title": "Haokang Seed - Pending Screening",
        "country": "Brazil",
        "languages": ["Portuguese"],
        "description": (
            "<h3>Pending Screening Demo</h3>"
            "<p>Seeded role used to show a candidate waiting in the initial screening list.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.ASSESSMENT_REVIEW,
        "title": "Haokang Seed - Assessment Review",
        "country": "Brazil",
        "languages": ["Portuguese"],
        "assessment_enabled": True,
        "description": (
            "<h3>Assessment Review Demo</h3>"
            "<p>Seeded role used to show a candidate whose assessment has been submitted.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.SCREENING_PASSED,
        "title": "Haokang Seed - Screening Passed",
        "country": "Brazil",
        "languages": ["Portuguese"],
        "assessment_enabled": True,
        "description": (
            "<h3>Screening Passed Demo</h3>"
            "<p>Seeded role used to show a candidate approved by screening.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.CONTRACT_POOL,
        "title": "Haokang Seed - Contract Pool",
        "country": "Mexico",
        "languages": ["Spanish"],
        "description": (
            "<h3>Contract Pool Demo</h3>"
            "<p>Seeded role used to show a candidate in the contract workflow.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.ACTIVE,
        "title": "Haokang Seed - Active",
        "country": "United States",
        "languages": ["English"],
        "description": (
            "<h3>Active Demo</h3>"
            "<p>Seeded role used to show a candidate who has successfully onboarded.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.REJECTED,
        "title": "Haokang Seed - Rejected",
        "country": "Brazil",
        "languages": ["Portuguese"],
        "assessment_enabled": True,
        "description": (
            "<h3>Rejected Demo</h3>"
            "<p>Seeded role used to show a candidate rejected from the assessment stage.</p>"
        ),
    },
    {
        "stage": RecruitmentStage.REPLACED,
        "title": "Haokang Seed - Replaced",
        "country": "United States",
        "languages": ["English"],
        "description": (
            "<h3>Replaced Demo</h3>"
            "<p>Seeded role used to show a candidate replaced after becoming active.</p>"
        ),
    },
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _ensure_form_template(session) -> AdminFormTemplate:
    result = await session.execute(
        select(AdminFormTemplate).where(
            AdminFormTemplate.name == TEMPLATE_NAME,
            AdminFormTemplate.is_deleted.is_(False),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise RuntimeError("Candidate base form template was not created.")
    return template


async def _ensure_user(session) -> User:
    result = await session.execute(
        select(User).where(
            or_(
                User.username == USERNAME,
                User.email == EMAIL,
            )
        )
    )
    matches = list(result.scalars().all())
    user = next((item for item in matches if item.username == USERNAME), None) or (matches[0] if matches else None)
    if user is None:
        user = User(
            name=DISPLAY_NAME,
            username=USERNAME,
            email=EMAIL,
            hashed_password=get_password_hash(PASSWORD),
            profile_image_url=DEFAULT_USER_PROFILE_IMAGE_URL,
            data={"seed_key": SEED_KEY},
        )
        session.add(user)
    else:
        email_owner = next((item for item in matches if item.email == EMAIL and item.id != user.id), None)
        if email_owner is not None:
            raise RuntimeError(f"Email {EMAIL} is already used by user_id={email_owner.id}.")
        user.name = DISPLAY_NAME
        user.username = USERNAME
        user.email = EMAIL
        user.hashed_password = get_password_hash(PASSWORD)
        user.profile_image_url = DEFAULT_USER_PROFILE_IMAGE_URL
        user.data = {**(user.data or {}), "seed_key": SEED_KEY}
        user.is_deleted = False
        user.deleted_at = None
    await session.flush()
    await session.refresh(user)
    return user


async def _ensure_resume_asset(session, *, user_id: int) -> Asset:
    result = await session.execute(
        select(Asset).where(
            Asset.storage_key == RESUME_STORAGE_KEY,
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        asset = Asset(
            type="file",
            module="candidate",
            owner_type="user",
            owner_id=user_id,
            original_name="ruanhaokang-seed-resume.pdf",
            storage_key=RESUME_STORAGE_KEY,
            mime_type="application/pdf",
            file_size=128,
            data={"seed_key": SEED_KEY},
        )
        session.add(asset)
    else:
        asset.type = "file"
        asset.module = "candidate"
        asset.owner_type = "user"
        asset.owner_id = user_id
        asset.original_name = "ruanhaokang-seed-resume.pdf"
        asset.mime_type = "application/pdf"
        asset.file_size = 128
        asset.data = {**(asset.data or {}), "seed_key": SEED_KEY}
        asset.is_deleted = False
        asset.deleted_at = None
    await session.flush()
    await session.refresh(asset)
    return asset


async def _ensure_job(
    session,
    *,
    owner_admin_user_id: int,
    form_template: AdminFormTemplate,
    definition: dict[str, Any],
) -> Job:
    referral_bonus_model = await ensure_referral_bonus_model(session)
    company = await ensure_company(session, name=SEED_COMPANY_NAME)
    project = await ensure_company_project(session, company_id=company.id, name=SEED_PROJECT_NAME)
    result = await session.execute(
        select(Job).where(
            Job.title == definition["title"],
            Job.is_deleted.is_(False),
        )
    )
    job = result.scalar_one_or_none()
    data = {
        "seed_key": SEED_KEY,
        JOB_DATA_FORM_FIELDS_KEY: form_template.fields,
        JOB_DATA_AUTOMATION_RULES_KEY: {"combinator": "and", "rules": []},
        JOB_DATA_APPLICATION_SUMMARY_KEY: {"applicants": 1},
        JOB_DATA_SHOW_COMPENSATION_KEY: True,
        JOB_DATA_LANGUAGES_KEY: definition["languages"],
        JOB_DATA_CONTRACT_EXAMPLE_KEY: (
            f"<p><strong>{definition['title']} contract guide</strong></p>"
            "<p>This local seed contract example is for candidate portal preview only.</p>"
        ),
    }
    if job is None:
        job = Job(
            title=definition["title"],
            company_id=company.id,
            project_id=project.id,
            referral_bonus_model_id=referral_bonus_model.id,
            country=definition["country"],
            status=JobStatus.OPEN.value,
            work_mode="Remote",
            compensation_min=Decimal("6.00"),
            compensation_max=Decimal("10.00"),
            compensation_unit="Per Hour",
            description=definition["description"],
            applicant_count=1,
            owner_admin_user_id=owner_admin_user_id,
            form_template_id=form_template.id,
            assessment_enabled=bool(definition.get("assessment_enabled", False)),
            data=data,
        )
        session.add(job)
    else:
        job.company_id = company.id
        job.project_id = project.id
        job.referral_bonus_model_id = referral_bonus_model.id
        job.country = definition["country"]
        job.status = JobStatus.OPEN.value
        job.work_mode = "Remote"
        job.compensation_min = Decimal("6.00")
        job.compensation_max = Decimal("10.00")
        job.compensation_unit = "Per Hour"
        job.description = definition["description"]
        job.applicant_count = 1
        job.owner_admin_user_id = owner_admin_user_id
        job.form_template_id = form_template.id
        job.assessment_enabled = bool(definition.get("assessment_enabled", False))
        job.data = data
        job.is_deleted = False
        job.deleted_at = None
    await session.flush()
    await session.refresh(job)
    return job


async def _ensure_talent_profile(
    session,
    *,
    user: User,
    resume_asset_id: int,
) -> TalentProfile:
    result = await session.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == user.id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = result.scalar_one_or_none()
    if talent is None:
        talent = TalentProfile(
            user_id=user.id,
            full_name=DISPLAY_NAME,
            email=EMAIL,
            whatsapp="+86 138 0000 0000",
            nationality="China",
            location="China",
            native_languages="Chinese, English",
            additional_languages="Portuguese",
            education="Bachelor completed",
            resume_asset_id=resume_asset_id,
            note="Seeded local candidate used to preview every recruitment stage.",
            data={"seed_key": SEED_KEY},
        )
        session.add(talent)
    else:
        talent.full_name = DISPLAY_NAME
        talent.email = EMAIL
        talent.whatsapp = "+86 138 0000 0000"
        talent.nationality = "China"
        talent.location = "China"
        talent.native_languages = "Chinese, English"
        talent.additional_languages = "Portuguese"
        talent.education = "Bachelor completed"
        talent.resume_asset_id = resume_asset_id
        talent.note = "Seeded local candidate used to preview every recruitment stage."
        talent.data = {**(talent.data or {}), "seed_key": SEED_KEY}
        talent.is_deleted = False
        talent.deleted_at = None
    await session.flush()
    await session.refresh(talent)
    return talent


async def _ensure_application(
    session,
    *,
    user: User,
    job: Job,
    form_template: AdminFormTemplate,
    submitted_at: datetime,
) -> CandidateApplication:
    result = await session.execute(
        select(CandidateApplication)
        .where(
            CandidateApplication.user_id == user.id,
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
    )
    application = result.scalars().first()
    if application is None:
        application = CandidateApplication(
            user_id=user.id,
            job_id=job.id,
            form_template_id=form_template.id,
            job_snapshot_title=job.title,
            status="submitted",
            submitted_at=submitted_at,
            data={"seed_key": SEED_KEY, "submitted_items_count": len(_application_field_values(resume_asset_id=0))},
        )
        session.add(application)
    else:
        application.form_template_id = form_template.id
        application.job_snapshot_title = job.title
        application.status = "submitted"
        application.submitted_at = submitted_at
        application.data = {
            **(application.data or {}),
            "seed_key": SEED_KEY,
            "submitted_items_count": len(_application_field_values(resume_asset_id=0)),
        }
        application.is_deleted = False
        application.deleted_at = None
    await session.flush()
    await session.refresh(application)
    return application


def _application_field_values(*, resume_asset_id: int) -> list[dict[str, Any]]:
    return [
        {
            "field_key": CandidateFieldKey.FULL_NAME.value,
            "field_label": "Full Name",
            "field_type": "text",
            "raw_value": DISPLAY_NAME,
            "display_value": DISPLAY_NAME,
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.EMAIL.value,
            "field_label": "Email",
            "field_type": "email",
            "raw_value": EMAIL,
            "display_value": EMAIL,
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.WHATSAPP.value,
            "field_label": "WhatsApp",
            "field_type": "text",
            "raw_value": "+86 138 0000 0000",
            "display_value": "+86 138 0000 0000",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
            "field_label": "Which country do you reside in on a long-term basis?",
            "field_type": "select",
            "raw_value": "China",
            "display_value": "China",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.CITY.value,
            "field_label": "Which city do you currently live in?",
            "field_type": "text",
            "raw_value": "Shanghai",
            "display_value": "Shanghai",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.NATIONALITY.value,
            "field_label": "Nationality/Citizenship",
            "field_type": "select",
            "raw_value": "China",
            "display_value": "China",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.NATIVE_LANGUAGES.value,
            "field_label": "Please list all your native-level languages (in English)",
            "field_type": "multiselect",
            "raw_value": json.dumps(["Chinese", "English"]),
            "display_value": "Chinese, English",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.ADDITIONAL_LANGUAGES.value,
            "field_label": "Please list any additional languages you speak at a proficient level (in English).",
            "field_type": "multiselect",
            "raw_value": json.dumps(["Portuguese"]),
            "display_value": "Portuguese",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.ENGLISH_PROFICIENCY.value,
            "field_label": "What is your English proficiency level?",
            "field_type": "select",
            "raw_value": "fully_professional_proficiency",
            "display_value": "Fully professional proficiency (can work independently in English)",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.AGE_RANGE.value,
            "field_label": "Age Range",
            "field_type": "select",
            "raw_value": "26_30",
            "display_value": "26-30",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value,
            "field_label": "The maximum working hours per day",
            "field_type": "select",
            "raw_value": "4_8_hours",
            "display_value": "4-8 hours",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value,
            "field_label": "Do you accept to be paid by hours",
            "field_type": "select",
            "raw_value": "yes",
            "display_value": "Yes",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value,
            "field_label": "Expected Salary in USD (Per Hour)",
            "field_type": "select",
            "raw_value": "6_10",
            "display_value": "USD 6-10 / hour",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.EDUCATION_STATUS.value,
            "field_label": "What is your current education status?",
            "field_type": "select",
            "raw_value": "bachelor_completed",
            "display_value": "Bachelor completed",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE.value,
            "field_label": "How many experience do you have in AI data annotation?",
            "field_type": "select",
            "raw_value": "1_2_years",
            "display_value": "1-2 years",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP.value,
            "field_label": (
                "Will you now or in the future require visa sponsorship to participate "
                "in this independent contractor role?"
            ),
            "field_type": "select",
            "raw_value": "no_sponsorship_required",
            "display_value": "No, I do not require sponsorship now or in the future",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.RESUME_ATTACHMENT.value,
            "field_label": "Please upload your most updated comprehensive English Resume here.",
            "field_type": "file",
            "raw_value": "ruanhaokang-seed-resume.pdf",
            "display_value": "ruanhaokang-seed-resume.pdf",
            "asset_id": resume_asset_id,
        },
        {
            "field_key": CandidateFieldKey.JOB_SOURCE.value,
            "field_label": "How did you hear about this position?",
            "field_type": "select",
            "raw_value": "other",
            "display_value": "Other",
            "asset_id": None,
        },
        {
            "field_key": CandidateFieldKey.ADDITIONAL_INFORMATION.value,
            "field_label": (
                "Please feel free to use this space to share any additional relevant information "
                "that would support your application for this role."
            ),
            "field_type": "text",
            "raw_value": "Local seed data for checking candidate job statuses.",
            "display_value": "Local seed data for checking candidate job statuses.",
            "asset_id": None,
        },
    ]


async def _replace_application_field_values(
    session,
    *,
    application_id: int,
    resume_asset_id: int,
) -> None:
    await session.execute(
        delete(CandidateApplicationFieldValue).where(
            CandidateApplicationFieldValue.application_id == application_id,
        )
    )
    for sort_order, field in enumerate(_application_field_values(resume_asset_id=resume_asset_id)):
        session.add(
            CandidateApplicationFieldValue(
                application_id=application_id,
                field_key=field["field_key"],
                field_label=field["field_label"],
                field_type=field["field_type"],
                catalog_key=field["field_key"],
                raw_value=field["raw_value"],
                display_value=field["display_value"],
                asset_id=field["asset_id"],
                sort_order=sort_order,
            )
        )
    await session.flush()


def _stage_data(stage: RecruitmentStage, *, now: datetime) -> dict[str, Any]:
    base = {
        "seed_key": SEED_KEY,
        JobProgressDataKey.NOTE.value: f"Seeded local progress state: {stage.value}.",
    }
    if stage == RecruitmentStage.PENDING_SCREENING:
        return base
    if stage == RecruitmentStage.ASSESSMENT_REVIEW:
        return {
            **base,
            JobProgressDataKey.ASSESSMENT_INVITED_AT.value: (now - timedelta(days=5)).isoformat(),
            JobProgressDataKey.ASSESSMENT_SENT_AT.value: (now - timedelta(days=4)).isoformat(),
            JobProgressDataKey.ASSESSMENT_ATTACHMENT.value: "haokang-assessment-submission.xlsx",
            JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value: (now - timedelta(days=2)).isoformat(),
            JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value: [
                {
                    "attachment": "haokang-assessment-submission.xlsx",
                    "submitted_at": (now - timedelta(days=2)).isoformat(),
                }
            ],
            JobProgressDataKey.ASSESSMENT_RESULT.value: "待评审",
            JobProgressDataKey.ASSESSMENT_REVIEWER.value: "Seed Reviewer",
        }
    if stage == RecruitmentStage.SCREENING_PASSED:
        return {
            **base,
            JobProgressDataKey.ASSESSMENT_RESULT.value: "通过",
            JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value: "Seeded candidate passed screening.",
            JobProgressDataKey.QA_STATUS.value: "待质检",
            JobProgressDataKey.ACCEPTED_RATE.value: "8.50",
            JobProgressDataKey.SIGNING_STATUS.value: "待通知签合同",
            JobProgressDataKey.SALARY_CONFIRMED_AT.value: (now - timedelta(days=1)).isoformat(),
        }
    if stage == RecruitmentStage.CONTRACT_POOL:
        return {
            **base,
            JobProgressDataKey.ASSESSMENT_RESULT.value: "通过",
            JobProgressDataKey.ACCEPTED_RATE.value: "9.00",
            JobProgressDataKey.SIGNING_STATUS.value: "已通知人选签合同",
            JobProgressDataKey.CONTRACT_NUMBER.value: "HK-SEED-CONTRACT-001",
            JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT.value: "haokang-draft-contract.pdf",
            JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT.value: "haokang-signed-contract.pdf",
            JobProgressDataKey.SUBMITTED_CONTRACT_AT.value: (now - timedelta(hours=18)).isoformat(),
            JobProgressDataKey.CONTRACT_REVIEW.value: "待审核",
            JobProgressDataKey.SALARY_CONFIRMED_AT.value: (now - timedelta(days=2)).isoformat(),
        }
    if stage == RecruitmentStage.ACTIVE:
        return {
            **base,
            JobProgressDataKey.ASSESSMENT_RESULT.value: "通过",
            JobProgressDataKey.ACCEPTED_RATE.value: "10.00",
            JobProgressDataKey.ONBOARDING_STATUS.value: "成功签约",
            JobProgressDataKey.ONBOARDING_DATE.value: now.date().isoformat(),
            JobProgressDataKey.SALARY_CONFIRMED_AT.value: (now - timedelta(days=5)).isoformat(),
            JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value: (now - timedelta(days=1)).isoformat(),
        }
    if stage == RecruitmentStage.REJECTED:
        return {
            **base,
            JobProgressDataKey.REJECTED_FROM_STAGE.value: RecruitmentStage.ASSESSMENT_REVIEW.value,
            JobProgressDataKey.ASSESSMENT_RESULT.value: "未通过",
            JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value: "Seeded rejected case for local preview.",
            JobProgressDataKey.QA_STATUS.value: "不通过",
        }
    if stage == RecruitmentStage.REPLACED:
        return {
            **base,
            JobProgressDataKey.ONBOARDING_STATUS.value: "汰换",
            JobProgressDataKey.REPLACEMENT_REASON.value: "Seeded replaced case for local preview.",
            JobProgressDataKey.ONBOARDING_DATE.value: (now - timedelta(days=30)).date().isoformat(),
        }
    return base


async def _ensure_progress(
    session,
    *,
    user: User,
    job: Job,
    application: CandidateApplication,
    talent: TalentProfile,
    stage: RecruitmentStage,
    entered_stage_at: datetime,
) -> JobProgress:
    result = await session.execute(select(JobProgress).where(JobProgress.application_id == application.id))
    progress = result.scalar_one_or_none()
    data = _stage_data(stage, now=_utc_now())
    data[JobProgressDataKey.JOB_LANGUAGES.value] = (job.data or {}).get(JOB_DATA_LANGUAGES_KEY, [])
    if progress is None:
        progress = JobProgress(
            job_id=job.id,
            user_id=user.id,
            application_id=application.id,
            talent_profile_id=talent.id,
            current_stage=stage.value,
            screening_mode=RecruitmentScreeningMode.MANUAL.value,
            entered_stage_at=entered_stage_at,
            data=data,
        )
        session.add(progress)
    else:
        progress.job_id = job.id
        progress.user_id = user.id
        progress.talent_profile_id = talent.id
        progress.current_stage = stage.value
        progress.screening_mode = RecruitmentScreeningMode.MANUAL.value
        progress.entered_stage_at = entered_stage_at
        progress.data = data
        progress.is_deleted = False
        progress.deleted_at = None
    await session.flush()
    await session.refresh(progress)
    return progress


async def seed() -> dict[str, Any]:
    await seed_candidate_base_form_template()
    async with local_session() as session:
        form_template = await _ensure_form_template(session)
        role = await ensure_role(session)
        admin = await ensure_admin_user(session, role_id=role.id)
        user = await _ensure_user(session)
        resume_asset = await _ensure_resume_asset(session, user_id=user.id)
        talent = await _ensure_talent_profile(session, user=user, resume_asset_id=resume_asset.id)

        base_time = _utc_now() - timedelta(days=len(STAGE_CASES))
        rows: list[dict[str, Any]] = []
        latest_application: CandidateApplication | None = None
        for index, definition in enumerate(STAGE_CASES):
            submitted_at = base_time + timedelta(days=index)
            job = await _ensure_job(
                session,
                owner_admin_user_id=admin.id,
                form_template=form_template,
                definition=definition,
            )
            application = await _ensure_application(
                session,
                user=user,
                job=job,
                form_template=form_template,
                submitted_at=submitted_at,
            )
            await _replace_application_field_values(
                session,
                application_id=application.id,
                resume_asset_id=resume_asset.id,
            )
            progress = await _ensure_progress(
                session,
                user=user,
                job=job,
                application=application,
                talent=talent,
                stage=definition["stage"],
                entered_stage_at=submitted_at,
            )
            job.applicant_count = await _count_active_applications(session, job_id=job.id)
            rows.append(
                {
                    "job_id": int(job.id),
                    "job_title": job.title,
                    "application_id": int(application.id),
                    "job_progress_id": int(progress.id),
                    "current_stage": progress.current_stage,
                }
            )
            latest_application = application

        if latest_application is not None:
            talent.latest_applied_job_id = latest_application.job_id
            talent.latest_applied_job_title = latest_application.job_snapshot_title
            talent.latest_applied_at = latest_application.submitted_at
            talent.source_application_id = latest_application.id
            talent.last_merged_at = latest_application.submitted_at

        await session.commit()
        return {
            "seed_key": SEED_KEY,
            "candidate": {
                "id": int(user.id),
                "username": USERNAME,
                "email": EMAIL,
                "password": PASSWORD,
            },
            "talent_profile_id": int(talent.id),
            "jobs": rows,
        }


async def _count_active_applications(session, *, job_id: int) -> int:
    result = await session.execute(
        select(func.count(CandidateApplication.id)).where(
            CandidateApplication.job_id == job_id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    return int(result.scalar() or 0)


async def main() -> None:
    try:
        payload = await seed()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
