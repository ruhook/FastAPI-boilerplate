import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from src.app.modules.admin.form_template.model import AdminFormTemplate
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_TYPE_NORMAL,
    CONTRACT_TYPE_TEAM_LEADER,
)
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.const import JobStatus
from src.app.modules.job.model import Job
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.payment_record import service as payment_record_service
from src.app.modules.payment_record.const import (
    PAYMENT_PAYOUT_STATUS_PENDING,
    PAYMENT_TYPE_SALARY,
    PAYMENT_TYPE_TEAM_LEADER_BONUS,
)
from src.app.modules.payment_record.model import PaymentRecord
from src.app.modules.payment_record.service import (
    _build_auto_payable_data,
    _calculate_team_leader_salary_amount,
    _CalculatedPayable,
    _serialize_calculated_payable,
    _serialize_paid_payable_record,
    list_auto_payment_payables_for_admin,
)
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.referral_bonus_model.const import DEFAULT_REFERRAL_BONUS_CAP
from src.app.modules.referral_bonus_model.model import ReferralBonusModel
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.user.model import User

pytestmark = pytest.mark.no_database_cleanup


def test_team_leader_payable_serialization_exposes_base_pay_and_bonus_split() -> None:
    item = _CalculatedPayable(
        source_key="team_leader_bonus:2026-06:101",
        source_month="2026-06",
        payment_type=PAYMENT_TYPE_TEAM_LEADER_BONUS,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        amount=Decimal("451.88"),
        currency="USD",
        user_name="Leader User",
        user_email="leader@example.com",
        company_id=401,
        project_id=501,
        company_name="Payment Company",
        project_name="Payment Project",
        contract_ref_no="TL-2026-06",
        country="Thailand",
        language="Thai",
        work_hours=Decimal("110.00"),
        rate=None,
        bonus_multiplier=Decimal("0.30"),
        team_leader_base_pay=Decimal("418.88"),
        team_leader_bonus=Decimal("33.00"),
        source_record_count=2,
    )

    payload = _serialize_calculated_payable(item).model_dump()

    assert payload["amount"] == Decimal("451.88")
    assert payload["team_leader_base_pay"] == Decimal("418.88")
    assert payload["team_leader_bonus"] == Decimal("33.00")


def test_team_leader_salary_amount_adds_base_pay_to_bonus() -> None:
    amount, base_pay, bonus = _calculate_team_leader_salary_amount(
        base_pay=Decimal("418.88"),
        monthly_team_hours=Decimal("110.00"),
    )

    assert amount == Decimal("451.88")
    assert base_pay == Decimal("418.88")
    assert bonus == Decimal("33.00")


def test_paid_team_leader_payable_reads_split_amounts_from_metadata() -> None:
    item = _CalculatedPayable(
        source_key="team_leader_bonus:2026-06:101",
        source_month="2026-06",
        payment_type=PAYMENT_TYPE_TEAM_LEADER_BONUS,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        amount=Decimal("560.00"),
        currency="USD",
        user_name="Paid Leader",
        user_email="paid.leader@example.com",
        company_id=401,
        project_id=501,
        company_name="Payment Company",
        project_name="Payment Project",
        contract_ref_no="TL-PAID",
        country="Malaysia",
        language="Portuguese",
        work_hours=Decimal("200.00"),
        rate=None,
        bonus_multiplier=Decimal("0.30"),
        team_leader_base_pay=Decimal("500.00"),
        team_leader_bonus=Decimal("60.00"),
        source_record_count=1,
    )
    record = PaymentRecord(
        id=901,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        payment_type=PAYMENT_TYPE_TEAM_LEADER_BONUS,
        amount=Decimal("560.00"),
        currency="USD",
        paid_at=datetime(2026, 6, 30, 10, 0, tzinfo=UTC),
        external_platform="Wise",
        external_transaction_no=None,
        remark=None,
        user_snapshot_name="Paid Leader",
        user_snapshot_email="paid.leader@example.com",
        company_id=401,
        project_id=501,
        company_snapshot_name="Payment Company",
        project_snapshot_name="Payment Project",
        contract_snapshot_ref_no="TL-PAID",
        data=_build_auto_payable_data(item),
    )

    payload = _serialize_paid_payable_record(record)

    assert payload is not None
    assert payload.amount == Decimal("560.00")
    assert payload.team_leader_base_pay == Decimal("500.00")
    assert payload.team_leader_bonus == Decimal("60.00")


@pytest.mark.asyncio
async def test_pending_prior_month_payable_rolls_forward_with_current_month_items(monkeypatch) -> None:
    prior_month_payable = _CalculatedPayable(
        source_key="salary:2026-05:301",
        source_month="2026-05",
        payment_type=PAYMENT_TYPE_SALARY,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        amount=Decimal("80.00"),
        currency="USD",
        user_name="Candidate User",
        user_email="candidate@example.com",
        company_id=401,
        project_id=501,
        company_name="Payment Company",
        project_name="Payment Project",
        contract_ref_no="PRD-2026",
        country="Malaysia",
        language="Malay",
        work_hours=Decimal("20.00"),
        rate=Decimal("4.00"),
        bonus_multiplier=None,
        team_leader_base_pay=None,
        team_leader_bonus=None,
        source_record_count=1,
    )
    prior_month_record = PaymentRecord(
        id=901,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        payment_type=PAYMENT_TYPE_SALARY,
        amount=Decimal("80.00"),
        currency="USD",
        paid_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
        external_platform=None,
        external_transaction_no=None,
        remark="Deferred to next month",
        user_snapshot_name="Candidate User",
        user_snapshot_email="candidate@example.com",
        company_id=401,
        project_id=501,
        company_snapshot_name="Payment Company",
        project_snapshot_name="Payment Project",
        contract_snapshot_ref_no="PRD-2026",
        data=_build_auto_payable_data(prior_month_payable, payout_status=PAYMENT_PAYOUT_STATUS_PENDING),
    )
    prior_month_item = _serialize_paid_payable_record(prior_month_record)
    assert prior_month_item is not None

    current_month_payable = _CalculatedPayable(
        source_key="salary:2026-06:301",
        source_month="2026-06",
        payment_type=PAYMENT_TYPE_SALARY,
        user_id=101,
        talent_profile_id=201,
        contract_record_id=301,
        referral_record_id=None,
        amount=Decimal("120.00"),
        currency="USD",
        user_name="Candidate User",
        user_email="candidate@example.com",
        company_id=401,
        project_id=501,
        company_name="Payment Company",
        project_name="Payment Project",
        contract_ref_no="PRD-2026",
        country="Malaysia",
        language="Malay",
        work_hours=Decimal("30.00"),
        rate=Decimal("4.00"),
        bonus_multiplier=None,
        team_leader_base_pay=None,
        team_leader_bonus=None,
        source_record_count=1,
    )

    async def fake_load_paid_auto_payables(**kwargs):
        assert kwargs["payment_type"] is None
        return {prior_month_item.source_key: prior_month_item}

    async def fake_calculate_auto_payables(**kwargs):
        assert kwargs["cutoff_start"] == date(2026, 7, 1)
        assert kwargs["payment_type"] is None
        return [current_month_payable]

    monkeypatch.setattr(payment_record_service, "_load_paid_auto_payables", fake_load_paid_auto_payables)
    monkeypatch.setattr(payment_record_service, "_calculate_auto_payables", fake_calculate_auto_payables)

    result = await payment_record_service.list_auto_payment_payables_for_admin(
        db=None,
        page=1,
        page_size=20,
        month="2026-06",
        payout_status=PAYMENT_PAYOUT_STATUS_PENDING,
    )

    candidate_items = [item for item in result["items"] if item["user_id"] == 101]
    assert len(candidate_items) == 2
    assert {item["source_month"] for item in candidate_items} == {"2026-05", "2026-06"}
    assert {item["payout_status"] for item in candidate_items} == {PAYMENT_PAYOUT_STATUS_PENDING}
    assert result["summary"]["pending_count"] == 2


def _payable_filter_item(
    *,
    source_key: str,
    user_name: str,
    language: str,
    external_platform: str | None,
    amount: str,
    payout_status: str = "pending",
) -> payment_record_service.PaymentPayableRecordRead:
    return payment_record_service.PaymentPayableRecordRead(
        id=f"test:{source_key}",
        source_key=source_key,
        source_month="2026-06",
        payout_status=payout_status,
        payment_record_id=None,
        user_id=100,
        talent_profile_id=200,
        contract_record_id=300,
        referral_record_id=None,
        payment_type=PAYMENT_TYPE_SALARY,
        amount=Decimal(amount),
        currency="USD",
        paid_at=None,
        external_platform=external_platform,
        external_transaction_no=None,
        remark=None,
        user_name=user_name,
        user_email=f"{source_key}@example.com",
        company_id=400,
        project_id=500,
        company_name="Payment Company",
        project_name="Payment Project",
        contract_ref_no=f"CTR-{source_key}",
        country="Saudi Arabia",
        language=language,
        work_hours=Decimal("10.00"),
        rate=Decimal("5.00"),
        bonus_multiplier=None,
        team_leader_base_pay=None,
        team_leader_bonus=None,
        source_record_count=1,
        created_at=None,
        updated_at=None,
    )


def test_payable_advanced_filter_matches_payment_method_and_language() -> None:
    items = [
        _payable_filter_item(
            source_key="airtim-ar",
            user_name="Air User",
            language="Ar-SA",
            external_platform="AirTim",
            amount="50.00",
        ),
        _payable_filter_item(
            source_key="wise-en",
            user_name="Wise User",
            language="En-US",
            external_platform="Wise",
            amount="80.00",
        ),
    ]
    query = {
        "combinator": "and",
        "rules": [
            {"field": "externalPlatform", "operator": "=", "value": "AirTim"},
            {"field": "language", "operator": "=", "value": "Ar-SA"},
        ],
    }

    filtered = payment_record_service._apply_payable_advanced_filter(items, query)

    assert [item.user_name for item in filtered] == ["Air User"]


def test_payable_advanced_filter_supports_amount_comparison() -> None:
    items = [
        _payable_filter_item(
            source_key="small",
            user_name="Small User",
            language="Ar-SA",
            external_platform="AirTim",
            amount="50.00",
        ),
        _payable_filter_item(
            source_key="large",
            user_name="Large User",
            language="Ar-SA",
            external_platform="AirTim",
            amount="120.00",
        ),
    ]
    query = {
        "combinator": "and",
        "rules": [{"field": "amount", "operator": ">=", "value": 100}],
    }

    filtered = payment_record_service._apply_payable_advanced_filter(items, query)

    assert [item.user_name for item in filtered] == ["Large User"]


def test_payable_advanced_filter_rejects_unknown_fields() -> None:
    query = {
        "combinator": "and",
        "rules": [{"field": "unknownField", "operator": "=", "value": "x"}],
    }

    with pytest.raises(BadRequestException):
        payment_record_service._apply_payable_advanced_filter([], query)


async def _create_company_project(db_session, *, suffix: str) -> tuple[AdminCompany, AdminCompanyProject]:
    company = AdminCompany(name=f"Payment Company {suffix}", description=None, data={})
    db_session.add(company)
    await db_session.flush()
    project = AdminCompanyProject(company_id=company.id, name=f"Payment Project {suffix}", data={})
    db_session.add(project)
    await db_session.flush()
    return company, project


async def _create_admin_user(db_session, *, suffix: str) -> AdminUser:
    admin = AdminUser(
        name="Payment Admin",
        username=f"payadmin{suffix}"[:20],
        email=f"payment.admin.{suffix}@example.com",
        hashed_password="test",
        phone=None,
        note=None,
        status="enabled",
        profile_image_url="https://example.com/admin.png",
        is_superuser=True,
        role_id=None,
        data={},
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


async def _create_referral_bonus_model(db_session, *, suffix: str) -> ReferralBonusModel:
    model = ReferralBonusModel(
        name=f"Payment Referral Model {suffix}",
        status="active",
        currency="USD",
        reward_cap=DEFAULT_REFERRAL_BONUS_CAP,
        data={"milestones": []},
    )
    db_session.add(model)
    await db_session.flush()
    return model


async def _create_form_template(db_session, *, suffix: str) -> AdminFormTemplate:
    template = AdminFormTemplate(
        name=f"Payment Template {suffix}",
        description="Payment payable test template",
        fields=[],
        data={},
    )
    db_session.add(template)
    await db_session.flush()
    return template


async def _create_job_context(
    db_session,
    *,
    suffix: str,
    company: AdminCompany,
    project: AdminCompanyProject,
) -> tuple[Job, AdminFormTemplate, AdminUser]:
    admin = await _create_admin_user(db_session, suffix=suffix)
    referral_model = await _create_referral_bonus_model(db_session, suffix=suffix)
    template = await _create_form_template(db_session, suffix=suffix)
    job = Job(
        title=f"Payment Job {suffix}",
        company_id=company.id,
        project_id=project.id,
        referral_bonus_model_id=referral_model.id,
        country="Brazil",
        status=JobStatus.OPEN.value,
        work_mode="Remote",
        compensation_min=Decimal("2.00"),
        compensation_max=Decimal("8.00"),
        compensation_unit="Per Hour",
        description="<p>Payment payable test job</p>",
        applicant_count=0,
        owner_admin_user_id=admin.id,
        form_template_id=template.id,
        assessment_enabled=False,
        data={},
    )
    db_session.add(job)
    await db_session.flush()
    return job, template, admin


async def _create_user_with_talent(
    db_session,
    *,
    suffix: str,
    name: str,
    email_prefix: str,
    location: str = "Malaysia",
) -> tuple[User, TalentProfile]:
    email = f"{email_prefix}.{suffix}@example.com"
    user = User(
        name=name,
        username=f"{email_prefix}{suffix}"[:20],
        email=email,
        hashed_password="test",
        profile_image_url="https://example.com/avatar.png",
        data={},
    )
    db_session.add(user)
    await db_session.flush()
    talent = TalentProfile(
        user_id=user.id,
        full_name=name,
        email=email,
        whatsapp=None,
        nationality="Brazil",
        location=location,
        native_languages="Portuguese",
        additional_languages="English",
        data={},
    )
    db_session.add(talent)
    await db_session.flush()
    return user, talent


async def _create_application_progress(
    db_session,
    *,
    user: User,
    talent: TalentProfile,
    job: Job,
    template: AdminFormTemplate,
) -> JobProgress:
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
        talent_profile_id=talent.id,
        current_stage="onboarding",
        screening_mode="manual",
        data={},
    )
    db_session.add(progress)
    await db_session.flush()
    return progress


async def _create_contract(
    db_session,
    *,
    user: User,
    talent: TalentProfile,
    progress: JobProgress,
    company: AdminCompany,
    project: AdminCompanyProject,
    ref_no: str,
    contract_type: str,
    rate: str,
    base_pay: str | None = None,
) -> ContractRecord:
    contract = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=talent.id,
        application_id=progress.application_id,
        job_id=progress.job_id,
        job_progress_id=progress.id,
        service_customer_company_id=company.id,
        service_customer_project_id=project.id,
        agreement_ref_no=ref_no,
        contract_status=CONTRACT_STATUS_ACTIVE,
        contract_type=contract_type,
        contractor_name=user.name,
        rate=Decimal(rate),
        base_pay=Decimal(base_pay) if base_pay is not None else None,
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date(2026, 6, 1),
        data={},
    )
    db_session.add(contract)
    await db_session.flush()
    return contract


async def _create_timesheet(
    db_session,
    *,
    user: User,
    talent: TalentProfile,
    contract: ContractRecord,
    company: AdminCompany,
    project: AdminCompanyProject,
    hours: str,
    leader_user_id: int | None = None,
    work_date: date = date(2026, 6, 12),
    language: str = "Portuguese",
) -> ProjectTimesheetRecord:
    record = ProjectTimesheetRecord(
        work_date=work_date,
        company_id=company.id,
        project_id=project.id,
        sub_project_name="Default Sub Project",
        user_id=user.id,
        talent_profile_id=talent.id,
        contract_record_id=contract.id,
        user_name_snapshot=user.name,
        user_email_snapshot=user.email,
        team_leader_user_id=leader_user_id,
        language=language,
        work_type="Production",
        role_name="Rater",
        customer_duration_hours=Decimal(hours),
        candidate_duration_hours=Decimal(hours),
        data={},
    )
    db_session.add(record)
    await db_session.flush()
    return record


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_PAYMENT_DB_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="Set RUN_PAYMENT_DB_TESTS=true to run payment payable database integration tests.",
)
async def test_team_leader_gets_production_salary_and_separate_team_leader_salary(db_session):
    suffix = uuid4().hex[:8]
    company, project = await _create_company_project(db_session, suffix=suffix)
    job, template, _admin = await _create_job_context(db_session, suffix=suffix, company=company, project=project)
    leader, leader_talent = await _create_user_with_talent(
        db_session,
        suffix=suffix,
        name="Leader User",
        email_prefix="leader.payables",
        location="Thailand",
    )
    worker, worker_talent = await _create_user_with_talent(
        db_session,
        suffix=suffix,
        name="Worker User",
        email_prefix="worker.payables",
        location="Indonesia",
    )
    leader_progress = await _create_application_progress(
        db_session,
        user=leader,
        talent=leader_talent,
        job=job,
        template=template,
    )
    worker_progress = await _create_application_progress(
        db_session,
        user=worker,
        talent=worker_talent,
        job=job,
        template=template,
    )
    leader_contract = await _create_contract(
        db_session,
        user=leader,
        talent=leader_talent,
        progress=leader_progress,
        company=company,
        project=project,
        ref_no=f"TL-{suffix}",
        contract_type=CONTRACT_TYPE_TEAM_LEADER,
        rate="5.00",
        base_pay="418.88",
    )
    worker_contract = await _create_contract(
        db_session,
        user=worker,
        talent=worker_talent,
        progress=worker_progress,
        company=company,
        project=project,
        ref_no=f"PRD-{suffix}",
        contract_type=CONTRACT_TYPE_NORMAL,
        rate="3.00",
    )
    await _create_timesheet(
        db_session,
        user=leader,
        talent=leader_talent,
        contract=leader_contract,
        company=company,
        project=project,
        hours="10.00",
        leader_user_id=leader.id,
        language="Thai",
    )
    await _create_timesheet(
        db_session,
        user=worker,
        talent=worker_talent,
        contract=worker_contract,
        company=company,
        project=project,
        hours="100.00",
        leader_user_id=leader.id,
        language="Indonesian",
    )
    await db_session.commit()

    result = await list_auto_payment_payables_for_admin(
        db=db_session,
        page=1,
        page_size=50,
        month="2026-06",
    )

    leader_items = [item for item in result["items"] if item["user_id"] == leader.id]
    production = next(item for item in leader_items if item["payment_type"] == PAYMENT_TYPE_SALARY)
    team_leader = next(item for item in leader_items if item["payment_type"] == PAYMENT_TYPE_TEAM_LEADER_BONUS)
    assert production["amount"] == Decimal("50.00")
    assert production["team_leader_base_pay"] is None
    assert production["team_leader_bonus"] is None
    assert team_leader["amount"] == Decimal("451.88")
    assert team_leader["team_leader_base_pay"] == Decimal("418.88")
    assert team_leader["team_leader_bonus"] == Decimal("33.00")
    assert team_leader["work_hours"] == Decimal("110.00")
    assert team_leader["bonus_multiplier"] == Decimal("0.30")
