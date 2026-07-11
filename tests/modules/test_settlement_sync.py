from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.application.settlement import sync_settlement_month, sync_timesheet_change
from src.app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from src.app.modules.admin.form_template.model import AdminFormTemplate
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.commands import update_contract_record_for_admin
from src.app.modules.contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_TYPE_NORMAL,
    CONTRACT_TYPE_TEAM_LEADER,
)
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.const import JobStatus
from src.app.modules.job.model import Job
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.payable.model import Payable, PayableTimesheetSource
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.referral_bonus_model.const import DEFAULT_REFERRAL_BONUS_CAP
from src.app.modules.referral_bonus_model.model import ReferralBonusModel
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.user.model import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_salary_source(
    db: AsyncSession,
    *,
    owner_admin_user_id: int,
) -> ProjectTimesheetRecord:
    suffix = uuid4().hex[:10]
    company = AdminCompany(name=f"Settlement Company {suffix}", description=None, data={})
    db.add(company)
    await db.flush()
    project = AdminCompanyProject(company_id=company.id, name=f"Settlement Project {suffix}", data={})
    bonus_model = ReferralBonusModel(
        name=f"Settlement Bonus {suffix}",
        status="active",
        currency="USD",
        reward_cap=DEFAULT_REFERRAL_BONUS_CAP,
        data={"milestones": []},
    )
    template = AdminFormTemplate(
        name=f"Settlement Template {suffix}",
        description="Settlement test template",
        fields=[],
        data={},
    )
    user = User(
        name="Settlement Candidate",
        username=f"settle{suffix}"[:20],
        email=f"settlement.{suffix}@example.com",
        hashed_password="test-hash",
        profile_image_url="https://example.com/profile.png",
        data={},
    )
    db.add_all([project, bonus_model, template, user])
    await db.flush()
    talent = TalentProfile(
        user_id=user.id,
        full_name=user.name,
        email=user.email,
        nationality="Brazil",
        location="Brazil",
        native_languages="Portuguese",
        additional_languages="English",
        data={},
    )
    db.add(talent)
    await db.flush()
    job = Job(
        title=f"Settlement Job {suffix}",
        company_id=company.id,
        project_id=project.id,
        referral_bonus_model_id=bonus_model.id,
        country="Brazil",
        status=JobStatus.OPEN.value,
        work_mode="Remote",
        compensation_min=Decimal("5.00"),
        compensation_max=Decimal("5.00"),
        compensation_unit="Per Hour",
        description="Settlement integration test",
        applicant_count=0,
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        assessment_enabled=False,
        data={},
    )
    db.add(job)
    await db.flush()
    application = CandidateApplication(
        user_id=user.id,
        job_id=job.id,
        form_template_id=template.id,
        job_snapshot_title=job.title,
        status="submitted",
        data={},
    )
    db.add(application)
    await db.flush()
    progress = JobProgress(
        job_id=job.id,
        user_id=user.id,
        application_id=application.id,
        talent_profile_id=talent.id,
        current_stage="onboarding",
        screening_mode="manual",
        data={},
    )
    db.add(progress)
    await db.flush()
    contract = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=talent.id,
        application_id=application.id,
        job_id=job.id,
        job_progress_id=progress.id,
        service_customer_company_id=company.id,
        service_customer_project_id=project.id,
        agreement_ref_no=f"SETTLE-{suffix}",
        contract_status=CONTRACT_STATUS_ACTIVE,
        contract_type=CONTRACT_TYPE_NORMAL,
        contractor_name=user.name,
        rate=Decimal("5.00"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date(2026, 7, 1),
        data={},
    )
    db.add(contract)
    await db.flush()
    record = ProjectTimesheetRecord(
        company_id=company.id,
        project_id=project.id,
        sub_project_name="Settlement source",
        work_date=date(2026, 7, 10),
        user_id=user.id,
        talent_profile_id=talent.id,
        contract_record_id=contract.id,
        user_name_snapshot=user.name,
        user_email_snapshot=user.email,
        language="Portuguese",
        work_type="Production",
        output_quantity=Decimal("2.00"),
        customer_duration_hours=Decimal("2.00"),
        candidate_duration_hours=Decimal("2.00"),
        non_operational_duration_hours=Decimal("0.00"),
        data={},
    )
    db.add(record)
    await db.flush()
    return record


async def test_timesheet_changes_recalculate_one_pending_salary_payable(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    record = await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )

    first = await sync_settlement_month(db=db_session, settlement_month="2026-07")
    assert first.created_count == 1
    payable = (await db_session.scalars(select(Payable))).one()
    payable_id = payable.id
    assert payable.amount == Decimal("10.00")
    source = (await db_session.scalars(select(PayableTimesheetSource))).one()
    assert source.source_version == record.version
    assert source.amount_contribution_snapshot == Decimal("10.00")

    record.candidate_duration_hours = Decimal("3.00")
    await db_session.flush()
    await sync_timesheet_change(db=db_session, settlement_month="2026-07")
    await db_session.refresh(payable)
    source = (await db_session.scalars(select(PayableTimesheetSource))).one()
    assert payable.id == payable_id
    assert payable.amount == Decimal("15.00")
    assert source.source_version == record.version
    assert source.amount_contribution_snapshot == Decimal("15.00")

    record.is_deleted = True
    await db_session.flush()
    final = await sync_timesheet_change(db=db_session, settlement_month="2026-07")
    assert final.deleted_count == 1
    assert (await db_session.scalars(select(Payable))).all() == []
    assert (await db_session.scalars(select(PayableTimesheetSource))).all() == []


async def test_admin_sync_is_explicit_and_get_does_not_materialize_payables(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    await db_session.commit()

    before_sync = await client.get(
        "/api/v1/payables",
        headers=admin_auth_headers,
        params={"settlement_month": "2026-07"},
    )
    assert before_sync.status_code == 200, before_sync.text
    assert before_sync.json()["total"] == 0
    assert (await db_session.scalars(select(Payable))).all() == []

    sync_response = await client.post(
        "/api/v1/payables/sync",
        headers=admin_auth_headers,
        json={"settlement_month": "2026-07"},
    )
    assert sync_response.status_code == 200, sync_response.text
    assert sync_response.json()["created_count"] == 1
    await db_session.rollback()
    assert len((await db_session.scalars(select(Payable))).all()) == 1

    after_sync = await client.get(
        "/api/v1/payables",
        headers=admin_auth_headers,
        params={"settlement_month": "2026-07"},
    )
    assert after_sync.status_code == 200, after_sync.text
    assert after_sync.json()["total"] == 1
    await db_session.rollback()
    assert len((await db_session.scalars(select(Payable))).all()) == 1


async def test_team_leader_payable_uses_project_hours_and_base_pay(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    record = await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    worker_contract = await db_session.get(ContractRecord, record.contract_record_id)
    assert worker_contract is not None
    job = await db_session.get(Job, worker_contract.job_id)
    assert job is not None
    suffix = uuid4().hex[:10]
    leader = User(
        name="Settlement Leader",
        username=f"leader{suffix}"[:20],
        email=f"leader.{suffix}@example.com",
        hashed_password="test-hash",
        profile_image_url="https://example.com/leader.png",
        data={},
    )
    db_session.add(leader)
    await db_session.flush()
    leader_talent = TalentProfile(
        user_id=leader.id,
        full_name=leader.name,
        email=leader.email,
        nationality="Brazil",
        location="Brazil",
        data={},
    )
    db_session.add(leader_talent)
    await db_session.flush()
    application = CandidateApplication(
        user_id=leader.id,
        job_id=job.id,
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        status="submitted",
        data={},
    )
    db_session.add(application)
    await db_session.flush()
    progress = JobProgress(
        job_id=job.id,
        user_id=leader.id,
        application_id=application.id,
        talent_profile_id=leader_talent.id,
        current_stage="onboarding",
        screening_mode="manual",
        data={},
    )
    db_session.add(progress)
    await db_session.flush()
    leader_contract = ContractRecord(
        user_id=leader.id,
        user_snapshot_name=leader.name,
        user_snapshot_email=leader.email,
        talent_profile_id=leader_talent.id,
        application_id=application.id,
        job_id=job.id,
        job_progress_id=progress.id,
        service_customer_company_id=record.company_id,
        service_customer_project_id=record.project_id,
        agreement_ref_no=f"LEADER-{suffix}",
        contract_status=CONTRACT_STATUS_ACTIVE,
        contract_type=CONTRACT_TYPE_TEAM_LEADER,
        contractor_name=leader.name,
        rate=Decimal("8.00"),
        base_pay=Decimal("100.00"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date(2026, 7, 1),
        data={},
    )
    db_session.add(leader_contract)
    await db_session.flush()
    record.team_leader_user_id = leader.id
    await db_session.flush()

    result = await sync_settlement_month(db=db_session, settlement_month="2026-07")
    assert result.created_count == 2
    payables = list((await db_session.scalars(select(Payable).order_by(Payable.payment_type))).all())
    assert [(item.payment_type, item.amount) for item in payables] == [
        ("salary", Decimal("10.00")),
        ("team_leader_bonus", Decimal("100.60")),
    ]
    leader_payable = next(item for item in payables if item.payment_type == "team_leader_bonus")
    assert leader_payable.project_id == record.project_id
    assert leader_payable.calculation_snapshot["work_hours"] == "2.00"
    assert leader_payable.calculation_snapshot["base_pay"] == "100.00"
    assert leader_payable.calculation_snapshot["bonus"] == "0.60"


async def test_contract_rate_change_recalculates_pending_salary(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    record = await _create_salary_source(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    await sync_settlement_month(db=db_session, settlement_month="2026-07")
    payable = (await db_session.scalars(select(Payable))).one()
    assert payable.amount == Decimal("10.00")

    await update_contract_record_for_admin(
        contract_record_id=int(record.contract_record_id or 0),
        admin_user_id=int(superadmin_credentials["id"]),
        db=db_session,
        rate=Decimal("7.00"),
        update_rate=True,
    )

    await db_session.refresh(payable)
    assert payable.amount == Decimal("14.00")
    assert payable.calculation_snapshot["rate"] == "7.00"
