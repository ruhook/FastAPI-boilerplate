import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.company.model import AdminCompany
from ..app.modules.admin.company.service import (
    COMPANY_DATA_TIMESHEET_LANGUAGES_KEY,
    COMPANY_DATA_TIMESHEET_ROLES_KEY,
    COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY,
)
from ..app.modules.admin.form_template.model import AdminFormTemplate
from ..app.modules.admin.role.model import Role
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.contract_record.model import ContractRecord
from ..app.modules.contract_record.service import upsert_contract_record_for_progress
from ..app.modules.job.const import JobStatus
from ..app.modules.job.model import Job
from ..app.modules.job_progress.const import RecruitmentScreeningMode, RecruitmentStage
from ..app.modules.job_progress.model import JobProgress
from ..app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from ..app.modules.project_timesheet_record.schema import (
    ProjectTimesheetBatchCreateEntry,
    ProjectTimesheetBatchCreateRequest,
)
from ..app.modules.project_timesheet_record.service import create_project_timesheet_records
from ..app.modules.referral.model import ReferralRecord
from ..app.modules.referral.service import ensure_user_referral_code
from ..app.modules.referral_bonus_model.service import (
    REFERRAL_BONUS_MILESTONES_DATA_KEY,
    build_referral_bonus_snapshot,
    ensure_user_referral_profile_from_job,
)
from ..app.modules.talent_profile.model import TalentProfile
from ..app.modules.user.const import DEFAULT_USER_PROFILE_IMAGE_URL
from ..app.modules.user.model import User
from .seed_apply_demo_flow import (
    DICTIONARY_DEFINITIONS,
    ensure_company,
    ensure_company_project,
    ensure_dictionary,
    ensure_form_template,
    ensure_job,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEMO_ROLE_NAME = "工时联调管理员"
DEMO_ROLE_DESCRIPTION = "用于工时、合同与岗位联调的专用后台账号。"
DEMO_ROLE_PERMISSIONS = [
    "岗位管理",
    "合同管理",
    "工时记录",
    "总人才库",
    "公司管理",
    "常量字典",
    "报名表单策略",
]

DEMO_ADMIN_NAME = "Timesheet Demo Admin"
DEMO_ADMIN_USERNAME = "timesheetadmin"
DEMO_ADMIN_EMAIL = "timesheet-admin@example.com"
DEMO_ADMIN_PASSWORD = "TimesheetAdmin123!"

DEMO_COMPANY_NAME = "TMX Timesheet Demo Lab"
DEMO_PROJECT_NAME = "Timesheet Active Project"
DEMO_JOB_TITLE = "Timesheet Demo - 10 Active Contractors"
DEMO_JOB_DEFINITION = {
    "title": DEMO_JOB_TITLE,
    "company_name": DEMO_COMPANY_NAME,
    "project_name": DEMO_PROJECT_NAME,
    "country": "Brazil",
    "work_mode": "Remote",
    "description": (
        "<h3>Timesheet Demo - 10 Active Contractors</h3>"
        "<p>This job is seeded for validating the admin timesheet workspace end to end.</p>"
    ),
    "compensation_min": Decimal("8.50"),
    "compensation_max": Decimal("16.00"),
    "compensation_unit": "Per Hour",
}

DEMO_CANDIDATE_PASSWORD = "Candidate123!"
DEMO_TIMESHEET_SUBPROJECT_PREFIX = "TS Demo · "
DEMO_TIMESHEET_LANGUAGES = ["en-US", "es-MX", "ja-JP", "ar-EG", "fr-FR"]
DEMO_TIMESHEET_WORK_TYPES = ["Annotation", "Review", "QA", "Training", "Non-Operational"]
DEMO_TIMESHEET_ROLES = ["Annotator", "Reviewer", "QA Specialist", "Trainer", "Team Lead"]
DEMO_REFERRAL_WORKER_INDEXES = [1, 2, 3]

DEMO_CANDIDATE_PORTAL_USER = {
    "username": "timesheetviewer",
    "email": "timesheet.viewer@example.com",
    "display_name": "Timesheet Viewer",
    "full_name": "Olivia Wang",
    "nationality": "Singapore",
    "location": "Singapore",
}

DEMO_CANDIDATE_PORTAL_CONTRACTS = [
    {
        "key": "localization",
        "contract_index": 201,
        "project_name": "Localization Ops Department",
        "job_title": "C Portal Hours Demo - Localization Contract",
        "country": "Singapore",
        "rate": Decimal("12.25"),
        "status": "Active",
        "contract_type": "normal",
        "referral_rewards": [
            {
                "referred_candidate": "Maya Lee",
                "onboarding_date_offset": -12,
                "status": "Active",
                "work_hours": "8.50",
                "referral_earnings": "42.50",
            }
        ],
    },
    {
        "key": "quality",
        "contract_index": 202,
        "project_name": "Quality Review Department",
        "job_title": "C Portal Hours Demo - Quality Contract",
        "country": "Japan",
        "rate": Decimal("14.75"),
        "status": "Active",
        "contract_type": "normal",
        "referral_rewards": [
            {
                "referred_candidate": "Ravi Kumar",
                "onboarding_date_offset": -31,
                "status": "Active",
                "work_hours": "5.25",
                "referral_earnings": "26.25",
            }
        ],
    },
    {
        "key": "lead",
        "contract_index": 203,
        "project_name": "Local Team Lead Department",
        "job_title": "C Portal Hours Demo - Team Lead Contract",
        "country": "Brazil",
        "rate": Decimal("16.50"),
        "status": "Active",
        "contract_type": "team_leader",
    },
    {
        "key": "legacy",
        "contract_index": 204,
        "project_name": "Legacy Archive Department",
        "job_title": "C Portal Hours Demo - Legacy Contract",
        "country": "Mexico",
        "rate": Decimal("11.80"),
        "status": "Terminated",
        "contract_type": "normal",
    },
]

DEMO_WORKERS = [
    {
        "index": 1,
        "username": "tsdemo01",
        "email": "timesheet.worker.01@example.com",
        "display_name": "TS Demo 01",
        "full_name": "Ana Silva",
        "nationality": "Brazil",
        "location": "Sao Paulo",
        "rate": Decimal("9.50"),
    },
    {
        "index": 2,
        "username": "tsdemo02",
        "email": "timesheet.worker.02@example.com",
        "display_name": "TS Demo 02",
        "full_name": "Bruno Costa",
        "nationality": "Brazil",
        "location": "Rio de Janeiro",
        "rate": Decimal("10.00"),
    },
    {
        "index": 3,
        "username": "tsdemo03",
        "email": "timesheet.worker.03@example.com",
        "display_name": "TS Demo 03",
        "full_name": "Carla Mendes",
        "nationality": "Portugal",
        "location": "Lisbon",
        "rate": Decimal("10.50"),
    },
    {
        "index": 4,
        "username": "tsdemo04",
        "email": "timesheet.worker.04@example.com",
        "display_name": "TS Demo 04",
        "full_name": "Diego Ramirez",
        "nationality": "Mexico",
        "location": "Monterrey",
        "rate": Decimal("11.00"),
    },
    {
        "index": 5,
        "username": "tsdemo05",
        "email": "timesheet.worker.05@example.com",
        "display_name": "TS Demo 05",
        "full_name": "Elena Petrova",
        "nationality": "Bulgaria",
        "location": "Sofia",
        "rate": Decimal("11.50"),
    },
    {
        "index": 6,
        "username": "tsdemo06",
        "email": "timesheet.worker.06@example.com",
        "display_name": "TS Demo 06",
        "full_name": "Fatima Noor",
        "nationality": "Egypt",
        "location": "Cairo",
        "rate": Decimal("12.00"),
    },
    {
        "index": 7,
        "username": "tsdemo07",
        "email": "timesheet.worker.07@example.com",
        "display_name": "TS Demo 07",
        "full_name": "Haru Sato",
        "nationality": "Japan",
        "location": "Tokyo",
        "rate": Decimal("12.50"),
    },
    {
        "index": 8,
        "username": "tsdemo08",
        "email": "timesheet.worker.08@example.com",
        "display_name": "TS Demo 08",
        "full_name": "Iris Chen",
        "nationality": "Singapore",
        "location": "Singapore",
        "rate": Decimal("13.00"),
    },
    {
        "index": 9,
        "username": "tsdemo09",
        "email": "timesheet.worker.09@example.com",
        "display_name": "TS Demo 09",
        "full_name": "Jamal Rahman",
        "nationality": "Malaysia",
        "location": "Kuala Lumpur",
        "rate": Decimal("13.50"),
    },
    {
        "index": 10,
        "username": "tsdemo10",
        "email": "timesheet.worker.10@example.com",
        "display_name": "TS Demo 10",
        "full_name": "Kenji Mori",
        "nationality": "Japan",
        "location": "Osaka",
        "rate": Decimal("14.00"),
    },
]

DEMO_TIMESHEET_BATCHES = [
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}April Kickoff",
        "work_date_offset": 0,
        "language": "en-US",
        "project_link": "https://example.com/projects/timesheet-demo/april-kickoff",
        "customer_human_efficiency_minutes": Decimal("2.40"),
        "candidate_human_efficiency_minutes": Decimal("2.40"),
        "entries": [
            {
                "worker_index": 1,
                "work_type": "Annotation",
                "output_quantity": Decimal("120"),
                "candidate_duration_hours": Decimal("4.70"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0.40"),
                "poc_evaluation": "Steady pace and high accuracy.",
                "extra_notes": "Handled the April kickoff batch cleanly.",
            },
            {
                "worker_index": 2,
                "work_type": "Review",
                "output_quantity": Decimal("95"),
                "candidate_duration_hours": Decimal("4.10"),
                "role_name": "Reviewer",
                "non_operational_duration_hours": Decimal("0.30"),
                "poc_evaluation": "Reliable reviewer coverage.",
                "extra_notes": "Focused on final pass validation.",
            },
            {
                "worker_index": 3,
                "work_type": "QA",
                "output_quantity": Decimal("80"),
                "candidate_duration_hours": Decimal("3.60"),
                "role_name": "QA Specialist",
                "non_operational_duration_hours": Decimal("0.25"),
                "poc_evaluation": "Consistent QA notes.",
                "extra_notes": "Helped surface edge cases early.",
            },
        ],
    },
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}LATAM Burst",
        "work_date_offset": -1,
        "language": "es-MX",
        "project_link": "https://example.com/projects/timesheet-demo/latam-burst",
        "customer_human_efficiency_minutes": Decimal("3.00"),
        "candidate_human_efficiency_minutes": Decimal("3.00"),
        "entries": [
            {
                "worker_index": 4,
                "work_type": "Annotation",
                "output_quantity": Decimal("88"),
                "candidate_duration_hours": Decimal("4.20"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0.50"),
                "poc_evaluation": "Solid throughput for Spanish content.",
                "extra_notes": "Took the lead on LATAM priority samples.",
            },
            {
                "worker_index": 5,
                "work_type": "Review",
                "output_quantity": Decimal("76"),
                "candidate_duration_hours": Decimal("3.90"),
                "role_name": "Reviewer",
                "non_operational_duration_hours": Decimal("0.35"),
                "poc_evaluation": "Careful review pass.",
                "extra_notes": "Raised terminology consistency issues.",
            },
            {
                "worker_index": 6,
                "work_type": "Training",
                "output_quantity": Decimal("42"),
                "candidate_duration_hours": Decimal("2.80"),
                "role_name": "Trainer",
                "non_operational_duration_hours": Decimal("0.60"),
                "poc_evaluation": "Helpful onboarding support.",
                "extra_notes": "Prepared the batch guide for the new cohort.",
            },
        ],
    },
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}JP Review Wave",
        "work_date_offset": -6,
        "language": "ja-JP",
        "project_link": "https://example.com/projects/timesheet-demo/jp-review-wave",
        "customer_human_efficiency_minutes": Decimal("2.10"),
        "candidate_human_efficiency_minutes": Decimal("2.10"),
        "entries": [
            {
                "worker_index": 7,
                "work_type": "QA",
                "output_quantity": Decimal("110"),
                "candidate_duration_hours": Decimal("4.30"),
                "role_name": "QA Specialist",
                "non_operational_duration_hours": Decimal("0.20"),
                "poc_evaluation": "Very detail-oriented review.",
                "extra_notes": "Caught repeated punctuation issues.",
            },
            {
                "worker_index": 8,
                "work_type": "Annotation",
                "output_quantity": Decimal("130"),
                "candidate_duration_hours": Decimal("4.75"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0.25"),
                "poc_evaluation": "Balanced speed and quality.",
                "extra_notes": "Picked up overflow tasks from the QA queue.",
            },
            {
                "worker_index": 10,
                "work_type": "Review",
                "output_quantity": Decimal("90"),
                "candidate_duration_hours": Decimal("3.85"),
                "role_name": "Reviewer",
                "non_operational_duration_hours": Decimal("0.40"),
                "poc_evaluation": "Strong reviewer support.",
                "extra_notes": "Handled the Japanese escalation batch.",
            },
        ],
    },
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}Arabic Coverage",
        "work_date_offset": -18,
        "language": "ar-EG",
        "project_link": "https://example.com/projects/timesheet-demo/arabic-coverage",
        "customer_human_efficiency_minutes": Decimal("2.80"),
        "candidate_human_efficiency_minutes": Decimal("2.80"),
        "entries": [
            {
                "worker_index": 6,
                "work_type": "Annotation",
                "output_quantity": Decimal("98"),
                "candidate_duration_hours": Decimal("4.55"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0.45"),
                "poc_evaluation": "Reliable Arabic batch delivery.",
                "extra_notes": "Kept quality stable across shifted priorities.",
            },
            {
                "worker_index": 9,
                "work_type": "QA",
                "output_quantity": Decimal("64"),
                "candidate_duration_hours": Decimal("3.20"),
                "role_name": "QA Specialist",
                "non_operational_duration_hours": Decimal("0.35"),
                "poc_evaluation": "Clear QA handoff notes.",
                "extra_notes": "Followed up quickly on fixes.",
            },
        ],
    },
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}Quarter Closeout",
        "work_date_offset": -39,
        "language": "fr-FR",
        "project_link": "https://example.com/projects/timesheet-demo/quarter-closeout",
        "customer_human_efficiency_minutes": Decimal("3.20"),
        "candidate_human_efficiency_minutes": Decimal("3.20"),
        "entries": [
            {
                "worker_index": 3,
                "work_type": "Review",
                "output_quantity": Decimal("74"),
                "candidate_duration_hours": Decimal("4.10"),
                "role_name": "Reviewer",
                "non_operational_duration_hours": Decimal("0.50"),
                "poc_evaluation": "Maintained quality through quarter close.",
                "extra_notes": "Supported French retrospective sampling.",
            },
            {
                "worker_index": 5,
                "work_type": "Training",
                "output_quantity": Decimal("30"),
                "candidate_duration_hours": Decimal("2.50"),
                "role_name": "Trainer",
                "non_operational_duration_hours": Decimal("0.70"),
                "poc_evaluation": "Good enablement support.",
                "extra_notes": "Prepared a short SOP refresher.",
            },
            {
                "worker_index": 8,
                "work_type": "Non-Operational",
                "output_quantity": Decimal("12"),
                "candidate_duration_hours": Decimal("1.60"),
                "role_name": "Team Lead",
                "non_operational_duration_hours": Decimal("1.20"),
                "poc_evaluation": "Handled coordination tasks smoothly.",
                "extra_notes": "Covered alignment and escalation work.",
            },
        ],
    },
    {
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}Archive Sample",
        "work_date_offset": -96,
        "language": "en-US",
        "project_link": "https://example.com/projects/timesheet-demo/archive-sample",
        "customer_human_efficiency_minutes": Decimal("2.60"),
        "candidate_human_efficiency_minutes": Decimal("2.60"),
        "entries": [
            {
                "worker_index": 1,
                "work_type": "QA",
                "output_quantity": Decimal("52"),
                "candidate_duration_hours": Decimal("2.90"),
                "role_name": "QA Specialist",
                "non_operational_duration_hours": Decimal("0.20"),
                "poc_evaluation": "Historical baseline sample.",
                "extra_notes": "Useful for long-range dashboard checks.",
            },
            {
                "worker_index": 10,
                "work_type": "Annotation",
                "output_quantity": Decimal("84"),
                "candidate_duration_hours": Decimal("3.75"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0.35"),
                "poc_evaluation": "Good legacy coverage sample.",
                "extra_notes": "Included to validate older date filters.",
            },
        ],
    },
]

DEMO_CANDIDATE_PORTAL_TIMESHEET_BATCHES = [
    {
        "contract_key": "localization",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-DL-902 Voice Batch",
        "work_date_offset": 0,
        "language": "en-US",
        "project_link": "https://example.com/projects/candidate-portal/dl-902",
        "customer_human_efficiency_minutes": Decimal("2.40"),
        "candidate_human_efficiency_minutes": Decimal("2.40"),
        "work_type": "Annotation",
        "output_quantity": Decimal("138"),
        "candidate_duration_hours": Decimal("5.50"),
        "role_name": "Annotator",
        "non_operational_duration_hours": Decimal("0.20"),
        "poc_evaluation": "Strong completion rate for the English batch.",
        "extra_notes": "Fresh record for All Time and Today checks.",
    },
    {
        "contract_key": "localization",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-DL-915 LATAM Review",
        "work_date_offset": -7,
        "language": "es-MX",
        "project_link": "https://example.com/projects/candidate-portal/dl-915",
        "customer_human_efficiency_minutes": Decimal("2.80"),
        "candidate_human_efficiency_minutes": Decimal("2.80"),
        "work_type": "Review",
        "output_quantity": Decimal("86"),
        "candidate_duration_hours": Decimal("3.75"),
        "role_name": "Reviewer",
        "non_operational_duration_hours": Decimal("0.35"),
        "poc_evaluation": "Good Spanish review coverage.",
        "extra_notes": "Included for Last 7 Days and custom ranges.",
    },
    {
        "contract_key": "localization",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-DL-928 French Archive",
        "work_date_offset": -38,
        "language": "fr-FR",
        "project_link": "https://example.com/projects/candidate-portal/dl-928",
        "customer_human_efficiency_minutes": Decimal("3.10"),
        "candidate_human_efficiency_minutes": Decimal("3.10"),
        "work_type": "QA",
        "output_quantity": Decimal("54"),
        "candidate_duration_hours": Decimal("2.90"),
        "role_name": "QA Specialist",
        "non_operational_duration_hours": Decimal("0.15"),
        "poc_evaluation": "Useful older record for month filters.",
        "extra_notes": "Makes All Time differ from recent ranges.",
    },
    {
        "contract_key": "quality",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-QA-104 JP Audit",
        "work_date_offset": -2,
        "language": "ja-JP",
        "project_link": "https://example.com/projects/candidate-portal/qa-104",
        "customer_human_efficiency_minutes": Decimal("2.20"),
        "candidate_human_efficiency_minutes": Decimal("2.20"),
        "work_type": "QA",
        "output_quantity": Decimal("120"),
        "candidate_duration_hours": Decimal("4.80"),
        "role_name": "QA Specialist",
        "non_operational_duration_hours": Decimal("0.30"),
        "poc_evaluation": "Accurate Japanese audit handoff.",
        "extra_notes": "Second active contract, different language.",
    },
    {
        "contract_key": "quality",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-QA-211 Arabic Review",
        "work_date_offset": -20,
        "language": "ar-EG",
        "project_link": "https://example.com/projects/candidate-portal/qa-211",
        "customer_human_efficiency_minutes": Decimal("2.60"),
        "candidate_human_efficiency_minutes": Decimal("2.60"),
        "work_type": "Review",
        "output_quantity": Decimal("74"),
        "candidate_duration_hours": Decimal("3.35"),
        "role_name": "Reviewer",
        "non_operational_duration_hours": Decimal("0.25"),
        "poc_evaluation": "Stable Arabic review throughput.",
        "extra_notes": "Mid-range historical sample.",
    },
    {
        "contract_key": "lead",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-TL-301 Weekly Sync",
        "work_date_offset": -1,
        "language": "en-US",
        "project_link": "https://example.com/projects/candidate-portal/tl-301",
        "customer_human_efficiency_minutes": Decimal("1.00"),
        "candidate_human_efficiency_minutes": Decimal("1.00"),
        "work_type": "Non-Operational",
        "output_quantity": Decimal("1"),
        "candidate_duration_hours": Decimal("3.00"),
        "role_name": "Team Lead",
        "non_operational_duration_hours": Decimal("1.00"),
        "poc_evaluation": "Local team lead coordination.",
        "extra_notes": "Should appear under Local Team Leader.",
    },
    {
        "contract_key": "lead",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-TL-322 LATAM Coaching",
        "work_date_offset": -65,
        "language": "es-MX",
        "project_link": "https://example.com/projects/candidate-portal/tl-322",
        "customer_human_efficiency_minutes": Decimal("1.00"),
        "candidate_human_efficiency_minutes": Decimal("1.00"),
        "work_type": "Training",
        "output_quantity": Decimal("1"),
        "candidate_duration_hours": Decimal("2.25"),
        "role_name": "Team Lead",
        "non_operational_duration_hours": Decimal("0.75"),
        "poc_evaluation": "Historical lead coaching session.",
        "extra_notes": "Inactive contract with older team lead hours.",
    },
    {
        "contract_key": "legacy",
        "sub_project_name": f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}CP-LEG-118 Archive QA",
        "work_date_offset": -80,
        "language": "es-MX",
        "project_link": "https://example.com/projects/candidate-portal/legacy-118",
        "customer_human_efficiency_minutes": Decimal("2.75"),
        "candidate_human_efficiency_minutes": Decimal("2.75"),
        "work_type": "QA",
        "output_quantity": Decimal("66"),
        "candidate_duration_hours": Decimal("3.65"),
        "role_name": "QA Specialist",
        "non_operational_duration_hours": Decimal("0.30"),
        "poc_evaluation": "Historical inactive contract QA record.",
        "extra_notes": "Used to validate inactive contract data toggle.",
    },
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def ensure_role(session) -> Role:
    result = await session.execute(select(Role).where(Role.name == DEMO_ROLE_NAME))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(
            name=DEMO_ROLE_NAME,
            description=DEMO_ROLE_DESCRIPTION,
            enabled=True,
            permissions=DEMO_ROLE_PERMISSIONS,
            data={},
        )
        session.add(role)
    else:
        role.description = DEMO_ROLE_DESCRIPTION
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
            note="Seeded admin for timesheet demo flow.",
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
        admin.note = "Seeded admin for timesheet demo flow."
        admin.status = "enabled"
        admin.profile_image_url = DEFAULT_ADMIN_PROFILE_IMAGE_URL
        admin.is_superuser = False
        admin.role_id = role_id
        admin.is_deleted = False
        admin.deleted_at = None
    await session.flush()
    await session.refresh(admin)
    return admin


async def ensure_company_timesheet_config(session, *, company: AdminCompany) -> AdminCompany:
    next_data = dict(company.data or {})
    next_data[COMPANY_DATA_TIMESHEET_LANGUAGES_KEY] = list(DEMO_TIMESHEET_LANGUAGES)
    next_data[COMPANY_DATA_TIMESHEET_WORK_TYPES_KEY] = list(DEMO_TIMESHEET_WORK_TYPES)
    next_data[COMPANY_DATA_TIMESHEET_ROLES_KEY] = list(DEMO_TIMESHEET_ROLES)
    company.data = next_data
    await session.flush()
    await session.refresh(company)
    return company


async def ensure_candidate_user(session, definition: dict[str, Any]) -> User:
    result = await session.execute(
        select(User).where(
            or_(
                User.username == definition["username"],
                User.email == definition["email"],
            )
        )
    )
    user = result.scalar_one_or_none()
    hashed_password = get_password_hash(DEMO_CANDIDATE_PASSWORD)
    if user is None:
        user = User(
            name=definition["display_name"],
            username=definition["username"],
            email=definition["email"],
            hashed_password=hashed_password,
            profile_image_url=DEFAULT_USER_PROFILE_IMAGE_URL,
            data={},
        )
        session.add(user)
    else:
        user.name = definition["display_name"]
        user.username = definition["username"]
        user.email = definition["email"]
        user.hashed_password = hashed_password
        user.profile_image_url = DEFAULT_USER_PROFILE_IMAGE_URL
        user.is_deleted = False
        user.deleted_at = None
    await session.flush()
    await session.refresh(user)
    return user


async def ensure_talent_profile(
    session,
    *,
    user: User,
    definition: dict[str, Any],
    job: Job,
    applied_at: datetime,
) -> TalentProfile:
    result = await session.execute(select(TalentProfile).where(TalentProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = TalentProfile(
            user_id=user.id,
            full_name=definition["full_name"],
            email=user.email,
            nationality=definition["nationality"],
            location=definition["location"],
            latest_applied_job_id=job.id,
            latest_applied_job_title=job.title,
            latest_applied_at=applied_at,
            data={},
        )
        session.add(profile)
    else:
        profile.full_name = definition["full_name"]
        profile.email = user.email
        profile.nationality = definition["nationality"]
        profile.location = definition["location"]
        profile.latest_applied_job_id = job.id
        profile.latest_applied_job_title = job.title
        profile.latest_applied_at = applied_at
        profile.is_deleted = False
        profile.deleted_at = None
    await session.flush()
    await session.refresh(profile)
    return profile


async def ensure_application(
    session,
    *,
    user: User,
    job: Job,
    form_template: AdminFormTemplate,
    submitted_at: datetime,
) -> CandidateApplication:
    result = await session.execute(
        select(CandidateApplication).where(
            CandidateApplication.user_id == user.id,
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        application = CandidateApplication(
            user_id=user.id,
            job_id=job.id,
            form_template_id=form_template.id,
            job_snapshot_title=job.title,
            status="submitted",
            submitted_at=submitted_at,
            data={},
        )
        session.add(application)
    else:
        application.form_template_id = form_template.id
        application.job_snapshot_title = job.title
        application.status = "submitted"
        application.submitted_at = submitted_at
        application.is_deleted = False
        application.deleted_at = None
    await session.flush()
    await session.refresh(application)
    return application


async def ensure_job_progress(
    session,
    *,
    user: User,
    application: CandidateApplication,
    talent_profile: TalentProfile,
    entered_stage_at: datetime,
) -> JobProgress:
    result = await session.execute(select(JobProgress).where(JobProgress.application_id == application.id))
    progress = result.scalar_one_or_none()
    if progress is None:
        progress = JobProgress(
            job_id=application.job_id,
            user_id=user.id,
            application_id=application.id,
            talent_profile_id=talent_profile.id,
            current_stage=RecruitmentStage.ACTIVE.value,
            screening_mode=RecruitmentScreeningMode.MANUAL.value,
            entered_stage_at=entered_stage_at,
            data={},
        )
        session.add(progress)
    else:
        progress.job_id = application.job_id
        progress.user_id = user.id
        progress.talent_profile_id = talent_profile.id
        progress.current_stage = RecruitmentStage.ACTIVE.value
        progress.screening_mode = RecruitmentScreeningMode.MANUAL.value
        progress.entered_stage_at = entered_stage_at
        progress.is_deleted = False
        progress.deleted_at = None
    await session.flush()
    await session.refresh(progress)
    return progress


async def ensure_active_contract_record(
    session,
    *,
    admin: AdminUser,
    job: Job,
    progress: JobProgress,
    definition: dict[str, Any],
    contract_index: int,
    effective_date: date,
) -> ContractRecord:
    existing_result = await session.execute(
        select(ContractRecord)
        .where(
            ContractRecord.job_progress_id == progress.id,
            ContractRecord.is_deleted.is_(False),
        )
        .order_by(
            ContractRecord.is_current.desc(),
            ContractRecord.version.desc(),
            ContractRecord.id.desc(),
        )
    )
    existing_records = existing_result.scalars().all()
    if existing_records:
        seed_current = existing_records[0]
        seed_current.is_current = True
        seed_current.contract_status = "Active"
        seed_current.updated_by_admin_user_id = admin.id
        for duplicate in existing_records[1:]:
            duplicate.is_deleted = True
            duplicate.deleted_at = _utc_now()
            duplicate.is_current = False
            duplicate.updated_by_admin_user_id = admin.id
        await session.flush()

    contract = await upsert_contract_record_for_progress(
        progress=progress,
        job=job,
        db=session,
        admin_user_id=admin.id,
        field_updates={
            "agreement_ref_no": f"TMX-TS-{contract_index:03d}",
            "rate": definition["rate"],
            "effective_date": effective_date,
            "end_date": effective_date + timedelta(days=365),
            "contract_status": "Active",
            "contract_type": definition.get("contract_type", "normal"),
            "is_current": True,
            "updated_by_admin_user_id": admin.id,
        },
        data_updates={
            "contract_review": "审核通过",
        },
    )
    await session.flush()
    await session.refresh(contract)
    return contract


def build_candidate_portal_job_definition(contract_definition: dict[str, Any]) -> dict[str, Any]:
    job_title = contract_definition["job_title"]
    project_name = contract_definition["project_name"]
    return {
        "title": job_title,
        "company_name": DEMO_COMPANY_NAME,
        "project_name": project_name,
        "country": contract_definition["country"],
        "work_mode": "Remote",
        "description": (
            f"<h3>{job_title}</h3><p>This role is seeded for validating the candidate Working Hours dashboard.</p>"
        ),
        "compensation_min": contract_definition["rate"],
        "compensation_max": contract_definition["rate"],
        "compensation_unit": "Per Hour",
        "contract_example": (
            f"<p><strong>Signing guide for {project_name}</strong></p>"
            "<p>This seeded contract is used by the candidate working-hours dashboard demo.</p>"
        ),
    }


def build_candidate_portal_referral_rewards(contract_definition: dict[str, Any]) -> list[dict[str, Any]]:
    rewards: list[dict[str, Any]] = []
    for raw_reward in contract_definition.get("referral_rewards") or []:
        rewards.append(
            {
                "referred_candidate": raw_reward["referred_candidate"],
                "onboarding_date": (
                    date.today() + timedelta(days=int(raw_reward.get("onboarding_date_offset", 0)))
                ).isoformat(),
                "status": raw_reward.get("status") or "Active",
                "work_hours": str(raw_reward.get("work_hours") or "0.00"),
                "referral_earnings": str(raw_reward.get("referral_earnings") or "0.00"),
            }
        )
    return rewards


async def ensure_candidate_portal_referral_records(
    session,
    *,
    referrer_user: User,
    contracts_by_index: dict[int, ContractRecord],
) -> list[dict[str, Any]]:
    profile_result = await session.execute(
        select(ContractRecord, Job)
        .join(Job, Job.id == ContractRecord.job_id)
        .where(
            ContractRecord.user_id == int(referrer_user.id),
            ContractRecord.contract_status == "Active",
            ContractRecord.is_deleted.is_(False),
        )
        .order_by(ContractRecord.id.asc())
        .limit(1)
    )
    profile_row = profile_result.first()
    if profile_row is None:
        return []
    referrer_contract, referrer_job = profile_row
    referrer_profile = await ensure_user_referral_profile_from_job(
        user_id=int(referrer_user.id),
        job=referrer_job,
        db=session,
        admin_user_id=None,
        contract_record=referrer_contract,
    )
    referral_snapshot = build_referral_bonus_snapshot(referrer_profile)
    referral_code = await ensure_user_referral_code(user_id=int(referrer_user.id), db=session)
    seeded_items: list[dict[str, Any]] = []
    for worker_index in DEMO_REFERRAL_WORKER_INDEXES:
        contract = contracts_by_index[int(worker_index)]
        referred_user = await session.get(User, int(contract.user_id))
        if referred_user is None or referred_user.is_deleted:
            continue

        result = await session.execute(
            select(ReferralRecord).where(
                ReferralRecord.referred_user_id == int(referred_user.id),
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = ReferralRecord(
                referrer_user_id=int(referrer_user.id),
                referred_user_id=int(referred_user.id),
                referred_talent_profile_id=int(contract.talent_profile_id) if contract.talent_profile_id else None,
                referrer_snapshot_name=referrer_user.name,
                referrer_snapshot_email=referrer_user.email,
                referred_snapshot_name=referred_user.name,
                referred_snapshot_email=referred_user.email,
                source_referral_code=referral_code,
                referral_bonus_model_id=referral_snapshot["referral_bonus_model_id"],
                model_snapshot_name=referral_snapshot["model_snapshot_name"],
                currency=referral_snapshot["currency"],
                reward_cap=Decimal(str(referral_snapshot["reward_cap"])),
                payout_status="tracking",
                data={REFERRAL_BONUS_MILESTONES_DATA_KEY: referral_snapshot["milestones"]},
            )
            session.add(record)
        else:
            record.referrer_user_id = int(referrer_user.id)
            record.referred_talent_profile_id = int(contract.talent_profile_id) if contract.talent_profile_id else None
            record.referrer_snapshot_name = referrer_user.name
            record.referrer_snapshot_email = referrer_user.email
            record.referred_snapshot_name = referred_user.name
            record.referred_snapshot_email = referred_user.email
            record.source_referral_code = referral_code
            record.referral_bonus_model_id = referral_snapshot["referral_bonus_model_id"]
            record.model_snapshot_name = referral_snapshot["model_snapshot_name"]
            record.currency = referral_snapshot["currency"]
            record.reward_cap = Decimal(str(referral_snapshot["reward_cap"]))
            record.paid_reward_amount = Decimal("0.00")
            record.payout_status = "tracking"
            record.last_paid_at = None
            record.last_paid_by_admin_user_id = None
            record.is_deleted = False
            record.deleted_at = None
            record.data = {REFERRAL_BONUS_MILESTONES_DATA_KEY: referral_snapshot["milestones"]}
        await session.flush()
        seeded_items.append(
            {
                "referral_record_id": int(record.id),
                "referred_user_id": int(referred_user.id),
                "referred_email": referred_user.email,
                "referred_name": referred_user.name,
                "agreement_ref_no": contract.agreement_ref_no,
            }
        )
    return seeded_items


async def ensure_candidate_portal_contracts(
    session,
    *,
    admin: AdminUser,
    form_template: AdminFormTemplate,
    company: AdminCompany,
) -> tuple[User, dict[str, ContractRecord], dict[str, Job], dict[str, str]]:
    user = await ensure_candidate_user(session, DEMO_CANDIDATE_PORTAL_USER)
    contracts_by_key: dict[str, ContractRecord] = {}
    jobs_by_key: dict[str, Job] = {}
    desired_status_by_key: dict[str, str] = {}
    base_now = _utc_now()

    for offset, contract_definition in enumerate(DEMO_CANDIDATE_PORTAL_CONTRACTS, start=1):
        job = await ensure_job(
            session,
            owner_admin_user_id=admin.id,
            form_template=form_template,
            definition=build_candidate_portal_job_definition(contract_definition),
        )
        if job.company_id != company.id:
            job.company_id = company.id
            await session.flush()
            await session.refresh(job)

        submitted_at = base_now - timedelta(days=offset * 18 + 90)
        profile = await ensure_talent_profile(
            session,
            user=user,
            definition={
                **DEMO_CANDIDATE_PORTAL_USER,
                "rate": contract_definition["rate"],
            },
            job=job,
            applied_at=submitted_at,
        )
        application = await ensure_application(
            session,
            user=user,
            job=job,
            form_template=form_template,
            submitted_at=submitted_at,
        )
        profile.source_application_id = application.id
        await session.flush()

        progress = await ensure_job_progress(
            session,
            user=user,
            application=application,
            talent_profile=profile,
            entered_stage_at=base_now - timedelta(days=offset * 18 + 84),
        )
        contract = await ensure_active_contract_record(
            session,
            admin=admin,
            job=job,
            progress=progress,
            definition={
                **DEMO_CANDIDATE_PORTAL_USER,
                "rate": contract_definition["rate"],
                "contract_type": contract_definition.get("contract_type", "normal"),
            },
            contract_index=int(contract_definition["contract_index"]),
            effective_date=date.today() - timedelta(days=offset * 42 + 120),
        )
        next_contract_data = dict(contract.data or {})
        next_contract_data["referral_rewards"] = build_candidate_portal_referral_rewards(contract_definition)
        contract.data = next_contract_data
        contract.contract_type = str(contract_definition.get("contract_type") or "normal")
        contracts_by_key[str(contract_definition["key"])] = contract
        jobs_by_key[str(contract_definition["key"])] = job
        desired_status_by_key[str(contract_definition["key"])] = str(contract_definition["status"])

    return user, contracts_by_key, jobs_by_key, desired_status_by_key


async def apply_candidate_portal_contract_statuses(
    session,
    *,
    admin: AdminUser,
    contracts_by_key: dict[str, ContractRecord],
    desired_status_by_key: dict[str, str],
) -> None:
    now = _utc_now()
    for key, contract in contracts_by_key.items():
        desired_status = desired_status_by_key.get(key) or "Active"
        contract.contract_status = desired_status
        contract.is_current = desired_status not in {"Terminated", "Expired"}
        contract.updated_at = now
        contract.updated_by_admin_user_id = admin.id
        if desired_status in {"Terminated", "Expired"} and contract.end_date is None:
            contract.end_date = date.today() - timedelta(days=14)
    await session.flush()


async def sync_job_applicant_count(session, *, job: Job) -> Job:
    result = await session.execute(
        select(func.count())
        .select_from(CandidateApplication)
        .where(
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    job.applicant_count = int(result.scalar() or 0)
    job.status = JobStatus.OPEN.value
    await session.flush()
    await session.refresh(job)
    return job


async def clear_seeded_timesheet_records(
    session,
    *,
    admin_user_id: int,
    company_id: int,
    project_id: int,
) -> int:
    result = await session.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.created_by_admin_user_id == admin_user_id,
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.is_deleted.is_(False),
            ProjectTimesheetRecord.sub_project_name.ilike(f"{DEMO_TIMESHEET_SUBPROJECT_PREFIX}%"),
        )
    )
    records = result.scalars().all()
    now = _utc_now()
    for record in records:
        record.is_deleted = True
        record.deleted_at = now
        record.updated_at = now
        record.updated_by_admin_user_id = admin_user_id
    await session.flush()
    return len(records)


async def seed_timesheet_records(
    session,
    *,
    admin: AdminUser,
    company_id: int,
    project_id: int,
    contracts_by_index: dict[int, ContractRecord],
) -> int:
    deleted_count = await clear_seeded_timesheet_records(
        session,
        admin_user_id=admin.id,
        company_id=company_id,
        project_id=project_id,
    )
    if deleted_count:
        logger.info("Soft-deleted %s existing seeded timesheet records.", deleted_count)

    today = date.today()
    created_count = 0
    for batch in DEMO_TIMESHEET_BATCHES:
        payload = ProjectTimesheetBatchCreateRequest(
            sub_project_name=batch["sub_project_name"],
            language=batch["language"],
            project_link=batch["project_link"],
            customer_human_efficiency_minutes=batch["customer_human_efficiency_minutes"],
            candidate_human_efficiency_minutes=batch["candidate_human_efficiency_minutes"],
            team_leader_user_id=int(contracts_by_index[int(batch["entries"][0]["worker_index"])].user_id),
            project_manager_admin_user_id=int(admin.id),
            entries=[
                ProjectTimesheetBatchCreateEntry(
                    work_date=today + timedelta(days=int(batch["work_date_offset"])),
                    contract_record_id=int(contracts_by_index[int(item["worker_index"])].id),
                    user_id=int(contracts_by_index[int(item["worker_index"])].user_id),
                    work_type=item["work_type"],
                    output_quantity=item["output_quantity"],
                    customer_duration_hours=(
                        batch["customer_human_efficiency_minutes"] * item["output_quantity"] / Decimal("60")
                    ).quantize(Decimal("0.01")),
                    candidate_duration_hours=item["candidate_duration_hours"],
                    role_name=item["role_name"],
                    non_operational_duration_hours=item["non_operational_duration_hours"],
                    extra_notes=item.get("extra_notes"),
                    poc_evaluation=item.get("poc_evaluation"),
                    note_asset_ids=[],
                )
                for item in batch["entries"]
            ],
        )
        result = await create_project_timesheet_records(
            company_id=company_id,
            project_id=project_id,
            payload=payload,
            db=session,
            admin_user_id=admin.id,
        )
        created_count += int(result["created_count"])

    return created_count


async def seed_candidate_portal_timesheet_records(
    session,
    *,
    admin: AdminUser,
    contracts_by_key: dict[str, ContractRecord],
) -> int:
    project_ids_to_clear = {
        int(contract.service_customer_project_id)
        for contract in contracts_by_key.values()
        if contract.service_customer_company_id is not None and contract.service_customer_project_id is not None
    }
    for project_id in project_ids_to_clear:
        sample_contract = next(
            contract
            for contract in contracts_by_key.values()
            if int(contract.service_customer_project_id) == project_id
        )
        deleted_count = await clear_seeded_timesheet_records(
            session,
            admin_user_id=admin.id,
            company_id=int(sample_contract.service_customer_company_id),
            project_id=project_id,
        )
        if deleted_count:
            logger.info("Soft-deleted %s candidate portal timesheet records for project %s.", deleted_count, project_id)

    today = date.today()
    created_count = 0
    for batch in DEMO_CANDIDATE_PORTAL_TIMESHEET_BATCHES:
        contract = contracts_by_key[str(batch["contract_key"])]
        payload = ProjectTimesheetBatchCreateRequest(
            sub_project_name=batch["sub_project_name"],
            language=batch["language"],
            project_link=batch["project_link"],
            customer_human_efficiency_minutes=batch["customer_human_efficiency_minutes"],
            candidate_human_efficiency_minutes=batch["candidate_human_efficiency_minutes"],
            team_leader_user_id=int(contract.user_id),
            project_manager_admin_user_id=int(admin.id),
            entries=[
                ProjectTimesheetBatchCreateEntry(
                    work_date=today + timedelta(days=int(batch["work_date_offset"])),
                    contract_record_id=int(contract.id),
                    user_id=int(contract.user_id),
                    work_type=batch["work_type"],
                    output_quantity=batch["output_quantity"],
                    customer_duration_hours=(
                        batch["customer_human_efficiency_minutes"] * batch["output_quantity"] / Decimal("60")
                    ).quantize(Decimal("0.01")),
                    candidate_duration_hours=batch["candidate_duration_hours"],
                    role_name=batch["role_name"],
                    non_operational_duration_hours=batch["non_operational_duration_hours"],
                    extra_notes=batch.get("extra_notes"),
                    poc_evaluation=batch.get("poc_evaluation"),
                    note_asset_ids=[],
                )
            ],
        )
        result = await create_project_timesheet_records(
            company_id=int(contract.service_customer_company_id),
            project_id=int(contract.service_customer_project_id),
            payload=payload,
            db=session,
            admin_user_id=admin.id,
        )
        created_count += int(result["created_count"])

    return created_count


async def count_active_contracts(
    session,
    *,
    job_id: int,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ContractRecord)
        .where(
            ContractRecord.job_id == job_id,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            ContractRecord.contract_status == "Active",
        )
    )
    return int(result.scalar() or 0)


async def count_timesheet_records(
    session,
    *,
    company_id: int,
    project_id: int,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ProjectTimesheetRecord)
        .where(
            ProjectTimesheetRecord.company_id == company_id,
            ProjectTimesheetRecord.project_id == project_id,
            ProjectTimesheetRecord.is_deleted.is_(False),
        )
    )
    return int(result.scalar() or 0)


async def main() -> None:
    try:
        async with local_session() as session:
            for definition in DICTIONARY_DEFINITIONS:
                await ensure_dictionary(session, definition)

            form_template = await ensure_form_template(session)
            role = await ensure_role(session)
            admin = await ensure_admin_user(session, role_id=role.id)
            job = await ensure_job(
                session,
                owner_admin_user_id=admin.id,
                form_template=form_template,
                definition=DEMO_JOB_DEFINITION,
            )

            company = await ensure_company(session, name=DEMO_COMPANY_NAME)
            company = await ensure_company_timesheet_config(session, company=company)
            project = await ensure_company_project(
                session,
                company_id=company.id,
                name=DEMO_PROJECT_NAME,
            )

            if job.company_id != company.id or job.project_id != project.id:
                job.company_id = company.id
                job.project_id = project.id
                await session.flush()
                await session.refresh(job)

            contracts_by_index: dict[int, ContractRecord] = {}
            base_now = _utc_now()

            for worker in DEMO_WORKERS:
                user = await ensure_candidate_user(session, worker)
                submitted_at = base_now - timedelta(days=worker["index"] + 20)
                profile = await ensure_talent_profile(
                    session,
                    user=user,
                    definition=worker,
                    job=job,
                    applied_at=submitted_at,
                )
                application = await ensure_application(
                    session,
                    user=user,
                    job=job,
                    form_template=form_template,
                    submitted_at=submitted_at,
                )
                profile.source_application_id = application.id
                await session.flush()

                progress = await ensure_job_progress(
                    session,
                    user=user,
                    application=application,
                    talent_profile=profile,
                    entered_stage_at=base_now - timedelta(days=worker["index"] + 14),
                )
                contract = await ensure_active_contract_record(
                    session,
                    admin=admin,
                    job=job,
                    progress=progress,
                    definition=worker,
                    contract_index=worker["index"],
                    effective_date=(date.today() - timedelta(days=worker["index"] + 60)),
                )
                contracts_by_index[int(worker["index"])] = contract

            (
                candidate_portal_user,
                candidate_portal_contracts_by_key,
                candidate_portal_jobs_by_key,
                candidate_portal_desired_status_by_key,
            ) = await ensure_candidate_portal_contracts(
                session,
                admin=admin,
                form_template=form_template,
                company=company,
            )
            referral_seed_items = await ensure_candidate_portal_referral_records(
                session,
                referrer_user=candidate_portal_user,
                contracts_by_index=contracts_by_index,
            )

            job = await sync_job_applicant_count(session, job=job)
            created_timesheet_count = await seed_timesheet_records(
                session,
                admin=admin,
                company_id=company.id,
                project_id=project.id,
                contracts_by_index=contracts_by_index,
            )
            created_candidate_portal_timesheet_count = await seed_candidate_portal_timesheet_records(
                session,
                admin=admin,
                contracts_by_key=candidate_portal_contracts_by_key,
            )
            await apply_candidate_portal_contract_statuses(
                session,
                admin=admin,
                contracts_by_key=candidate_portal_contracts_by_key,
                desired_status_by_key=candidate_portal_desired_status_by_key,
            )
            for candidate_portal_job in candidate_portal_jobs_by_key.values():
                await sync_job_applicant_count(session, job=candidate_portal_job)

            await session.commit()

            active_contract_count = await count_active_contracts(session, job_id=job.id)
            timesheet_record_count = await count_timesheet_records(
                session,
                company_id=company.id,
                project_id=project.id,
            )
            candidate_portal_timesheet_count = 0
            for contract in candidate_portal_contracts_by_key.values():
                candidate_portal_timesheet_count += await count_timesheet_records(
                    session,
                    company_id=int(contract.service_customer_company_id),
                    project_id=int(contract.service_customer_project_id),
                )
            candidate_portal_definition_by_key = {
                str(definition["key"]): definition for definition in DEMO_CANDIDATE_PORTAL_CONTRACTS
            }

            payload = {
                "admin": {
                    "username": DEMO_ADMIN_USERNAME,
                    "email": DEMO_ADMIN_EMAIL,
                    "password": DEMO_ADMIN_PASSWORD,
                    "role": DEMO_ROLE_NAME,
                },
                "candidate_password": DEMO_CANDIDATE_PASSWORD,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "timesheet_languages": DEMO_TIMESHEET_LANGUAGES,
                    "timesheet_work_types": DEMO_TIMESHEET_WORK_TYPES,
                    "timesheet_roles": DEMO_TIMESHEET_ROLES,
                },
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "timesheet_page_path": f"/timesheets/companies/{company.id}/projects/{project.id}",
                },
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "status": job.status,
                    "applicant_count": job.applicant_count,
                    "progress_page_path": f"/jobs/{job.id}/progress?stage=active",
                },
                "contracts": {
                    "active_count": active_contract_count,
                    "sample_agreement_refs": [
                        contracts_by_index[index].agreement_ref_no for index in sorted(contracts_by_index)[:5]
                    ],
                },
                "timesheets": {
                    "created_or_recreated_count": created_timesheet_count,
                    "total_active_records": timesheet_record_count,
                    "sub_project_prefix": DEMO_TIMESHEET_SUBPROJECT_PREFIX,
                },
                "candidate_portal_timesheet_viewer": {
                    "username": candidate_portal_user.username,
                    "email": candidate_portal_user.email,
                    "password": DEMO_CANDIDATE_PASSWORD,
                    "page_path": "/working-hours",
                    "referral_page_path": "/referral",
                    "earnings_page_path": "/earnings",
                    "company": company.name,
                    "created_or_recreated_count": created_candidate_portal_timesheet_count,
                    "total_active_records_across_projects": candidate_portal_timesheet_count,
                    "bonus_month": date.today().strftime("%Y-%m"),
                    "contracts": [
                        {
                            "key": key,
                            "job_title": candidate_portal_jobs_by_key[key].title,
                            "project_name": candidate_portal_definition_by_key[key]["project_name"],
                            "agreement_ref_no": contract.agreement_ref_no,
                            "contract_status": contract.contract_status,
                            "contract_type": contract.contract_type,
                            "is_current": contract.is_current,
                            "rate": format(contract.rate, "f") if contract.rate is not None else None,
                        }
                        for key, contract in candidate_portal_contracts_by_key.items()
                    ],
                    "languages": sorted({str(batch["language"]) for batch in DEMO_CANDIDATE_PORTAL_TIMESHEET_BATCHES}),
                    "referrals": {
                        "referral_count": len(referral_seed_items),
                        "items": referral_seed_items,
                    },
                },
                "workers": [
                    {
                        "full_name": worker["full_name"],
                        "email": worker["email"],
                        "agreement_ref_no": contracts_by_index[worker["index"]].agreement_ref_no,
                        "rate": format(worker["rate"], "f"),
                    }
                    for worker in DEMO_WORKERS
                ],
            }
            logger.info("Timesheet demo seed prepared successfully.")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
