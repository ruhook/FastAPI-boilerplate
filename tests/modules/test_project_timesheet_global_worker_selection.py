from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from src.app.modules.admin.form_template.model import AdminFormTemplate
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.const import CONTRACT_STATUS_ACTIVE
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.const import JobStatus
from src.app.modules.job.model import Job
from src.app.modules.job_progress.const import RecruitmentScreeningMode, RecruitmentStage
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.payable.model import Payable
from src.app.modules.project_timesheet_record.commands import create_project_timesheet_records
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.project_timesheet_record.queries import list_project_timesheet_workspace
from src.app.modules.project_timesheet_record.schema import ProjectTimesheetBatchCreateRequest
from src.app.modules.referral_bonus_model.const import (
    DEFAULT_REFERRAL_BONUS_CAP,
    DEFAULT_REFERRAL_BONUS_CURRENCY,
)
from src.app.modules.referral_bonus_model.model import ReferralBonusModel
from src.app.modules.user.model import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _get_superadmin(db_session: AsyncSession) -> AdminUser:
    result = await db_session.execute(select(AdminUser).where(AdminUser.is_superuser.is_(True)).limit(1))
    admin = result.scalar_one()
    return admin


async def _create_company_project(
    db_session: AsyncSession,
    *,
    suffix: str,
    name: str,
) -> tuple[AdminCompany, AdminCompanyProject]:
    company = AdminCompany(name=f"{name} Company {suffix}", description=None, data={})
    db_session.add(company)
    await db_session.flush()
    project = AdminCompanyProject(company_id=company.id, name=f"{name} Project {suffix}", data={})
    db_session.add(project)
    await db_session.flush()
    return company, project


async def _create_job_stack(
    db_session: AsyncSession,
    *,
    suffix: str,
    user: User,
    company: AdminCompany,
    project: AdminCompanyProject,
    admin: AdminUser,
) -> tuple[Job, JobProgress]:
    referral_model = ReferralBonusModel(
        name=f"Timesheet Referral {suffix}",
        status="active",
        currency=DEFAULT_REFERRAL_BONUS_CURRENCY,
        reward_cap=DEFAULT_REFERRAL_BONUS_CAP,
        data={"milestones": []},
    )
    template = AdminFormTemplate(
        name=f"Timesheet Template {suffix}",
        description=None,
        fields=[],
        data={},
    )
    db_session.add_all([referral_model, template])
    await db_session.flush()

    job = Job(
        title=f"Timesheet Job {suffix}",
        company_id=company.id,
        project_id=project.id,
        referral_bonus_model_id=referral_model.id,
        country="Brazil",
        status=JobStatus.OPEN.value,
        work_mode="Remote",
        compensation_min=Decimal("2.00"),
        compensation_max=Decimal("5.00"),
        compensation_unit="Per Hour",
        description="<p>Timesheet test job</p>",
        owner_admin_user_id=admin.id,
        form_template_id=template.id,
        assessment_enabled=False,
        data={},
    )
    db_session.add(job)
    await db_session.flush()

    application = CandidateApplication(
        user_id=user.id,
        job_id=job.id,
        form_template_id=template.id,
        job_snapshot_title=job.title,
        status="submitted",
        data={},
    )
    db_session.add(application)
    await db_session.flush()

    progress = JobProgress(
        job_id=job.id,
        user_id=user.id,
        application_id=application.id,
        talent_profile_id=None,
        current_stage=RecruitmentStage.ACTIVE.value,
        screening_mode=RecruitmentScreeningMode.MANUAL.value,
        data={},
    )
    db_session.add(progress)
    await db_session.flush()
    return job, progress


async def _create_active_contract(
    db_session: AsyncSession,
    *,
    suffix: str,
    name: str,
    user: User,
    company: AdminCompany,
    project: AdminCompanyProject,
    admin: AdminUser,
) -> ContractRecord:
    job, progress = await _create_job_stack(
        db_session,
        suffix=suffix,
        user=user,
        company=company,
        project=project,
        admin=admin,
    )
    contract = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=None,
        application_id=progress.application_id,
        job_id=job.id,
        job_progress_id=progress.id,
        job_snapshot_title=job.title,
        service_customer_company_id=company.id,
        service_customer_project_id=project.id,
        agreement_ref_no=f"TS-{suffix}",
        contract_status=CONTRACT_STATUS_ACTIVE,
        contract_type="normal",
        contractor_name=name,
        rate=Decimal("5.00"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        created_by_admin_user_id=admin.id,
        updated_by_admin_user_id=admin.id,
        data={},
    )
    db_session.add(contract)
    await db_session.flush()
    return contract


def _create_user(*, suffix: str, name: str) -> User:
    return User(
        name=name,
        username=f"ts{suffix}"[:20],
        email=f"timesheet.{suffix}@example.com",
        hashed_password="unused-test-hash",
        profile_image_url="https://example.com/avatar.png",
        data={},
    )


def _create_admin_user(*, suffix: str, name: str) -> AdminUser:
    return AdminUser(
        name=name,
        username=f"pm{suffix}"[:20],
        email=f"project.manager.{suffix}@example.com",
        hashed_password="unused-test-hash",
        status="enabled",
        profile_image_url="https://example.com/admin-avatar.png",
        is_superuser=False,
        data={},
    )


async def test_workspace_worker_and_team_leader_options_are_not_limited_to_current_project_or_company(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    admin = await _get_superadmin(db_session)
    current_company, current_project = await _create_company_project(db_session, suffix=suffix, name="Current")
    other_project_company, other_project = await _create_company_project(
        db_session,
        suffix=f"{suffix}b",
        name="SamePool",
    )
    other_company, other_company_project = await _create_company_project(
        db_session,
        suffix=f"{suffix}c",
        name="External",
    )

    project_worker = _create_user(suffix=f"worker{suffix}", name="Cross Project Worker")
    company_leader = _create_user(suffix=f"leader{suffix}", name="Cross Company Leader")
    db_session.add_all([project_worker, company_leader])
    await db_session.flush()
    worker_contract = await _create_active_contract(
        db_session,
        suffix=f"w{suffix}",
        name=project_worker.name,
        user=project_worker,
        company=other_project_company,
        project=other_project,
        admin=admin,
    )
    leader_contract = await _create_active_contract(
        db_session,
        suffix=f"l{suffix}",
        name=company_leader.name,
        user=company_leader,
        company=other_company,
        project=other_company_project,
        admin=admin,
    )
    await db_session.commit()

    workspace = await list_project_timesheet_workspace(
        company_id=current_company.id,
        project_id=current_project.id,
        db=db_session,
    )

    assert worker_contract.id in {worker["contract_record_id"] for worker in workspace["available_workers"]}
    assert leader_contract.user_id in {worker["user_id"] for worker in workspace["available_team_leader_workers"]}


async def test_create_timesheet_allows_worker_and_team_leader_from_other_company(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    admin = await _get_superadmin(db_session)
    current_company, current_project = await _create_company_project(db_session, suffix=suffix, name="Target")
    other_company, other_company_project = await _create_company_project(
        db_session,
        suffix=f"{suffix}o",
        name="External",
    )

    worker = _create_user(suffix=f"cw{suffix}", name="External Worker")
    leader = _create_user(suffix=f"cl{suffix}", name="External Leader")
    project_manager = _create_admin_user(suffix=suffix, name="Project Manager")
    db_session.add_all([worker, leader, project_manager])
    await db_session.flush()
    worker_contract = await _create_active_contract(
        db_session,
        suffix=f"cw{suffix}",
        name=worker.name,
        user=worker,
        company=other_company,
        project=other_company_project,
        admin=admin,
    )
    leader_contract = await _create_active_contract(
        db_session,
        suffix=f"cl{suffix}",
        name=leader.name,
        user=leader,
        company=other_company,
        project=other_company_project,
        admin=admin,
    )
    leader_contract.contract_type = "team_leader"
    leader_contract.base_pay = Decimal("100.00")
    await db_session.commit()

    payload = ProjectTimesheetBatchCreateRequest(
        idempotency_key=f"timesheet-{suffix}",
        sub_project_name="Cross company support",
        language="Arabic",
        project_link="",
        customer_human_efficiency_minutes=Decimal("5"),
        candidate_human_efficiency_minutes=Decimal("6"),
        team_leader_user_id=leader_contract.user_id,
        project_manager_admin_user_id=project_manager.id,
        entries=[
            {
                "work_date": date(2026, 6, 26),
                "contract_record_id": worker_contract.id,
                "user_id": worker.id,
                "work_type": "Production",
                "output_quantity": Decimal("12"),
                "customer_duration_hours": Decimal("1.00"),
                "candidate_duration_hours": Decimal("1.20"),
                "role_name": "Annotator",
                "non_operational_duration_hours": Decimal("0"),
            },
        ],
    )

    result = await create_project_timesheet_records(
        company_id=current_company.id,
        project_id=current_project.id,
        payload=payload,
        db=db_session,
        admin_user_id=admin.id,
    )

    duplicate_result = await create_project_timesheet_records(
        company_id=current_company.id,
        project_id=current_project.id,
        payload=payload,
        db=db_session,
        admin_user_id=admin.id,
    )

    assert result["created_count"] == 1
    assert result["record_ids"] == duplicate_result["record_ids"]
    timesheet_result = await db_session.execute(
        select(ProjectTimesheetRecord).where(
            ProjectTimesheetRecord.company_id == current_company.id,
            ProjectTimesheetRecord.project_id == current_project.id,
        )
    )
    timesheet_record = timesheet_result.scalar_one()
    assert timesheet_record.work_date == date(2026, 6, 26)
    assert timesheet_record.project_manager_admin_user_id == project_manager.id
    assert timesheet_record.project_manager_name_snapshot == project_manager.name
    assert timesheet_record.project_link is None
    payable_result = await db_session.execute(
        select(Payable).where(
            Payable.contract_record_id == worker_contract.id,
            Payable.settlement_month == "2026-06",
        )
    )
    salary_payable = payable_result.scalar_one()
    assert salary_payable.payment_type == "salary"
    assert salary_payable.amount == Decimal("6.00")
    leader_payable = (
        await db_session.scalars(
            select(Payable).where(
                Payable.user_id == leader.id,
                Payable.payment_type == "team_leader_bonus",
            )
        )
    ).one()
    assert leader_payable.project_id == current_project.id
    assert leader_payable.amount == Decimal("100.36")

    workspace = await list_project_timesheet_workspace(
        company_id=current_company.id,
        project_id=current_project.id,
        db=db_session,
    )
    assert workspace["records"][0]["project_manager_admin_user_id"] == project_manager.id
    assert workspace["records"][0]["project_manager_name"] == project_manager.name
    assert workspace["records"][0]["registrar_admin_user_id"] == admin.id
    assert workspace["records"][0]["registrar_name"] == admin.name
