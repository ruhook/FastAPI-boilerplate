from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.application.payouts import pay_payables, reverse_payment
from src.app.application.settlement import sync_settlement_month, sync_timesheet_change
from src.app.core.security import get_password_hash
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.model import Job
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.payable.commands import transition_payables
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable, PayableTimesheetSource
from src.app.modules.payment.schema import PayoutDetails
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.referral.model import ReferralRecord
from src.app.modules.referral.service import list_referrals_for_admin
from src.app.modules.referral_bonus_model.service import (
    REFERRAL_BONUS_MILESTONES_DATA_KEY,
    ensure_user_referral_profile_from_job,
)
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.user.model import User
from tests.modules.test_settlement_sync import _create_salary_source

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_referral_with_reached_milestones(
    db: AsyncSession,
    *,
    owner_admin_user_id: int,
) -> ReferralRecord:
    timesheet = await _create_salary_source(db, owner_admin_user_id=owner_admin_user_id)
    contract = await db.get(ContractRecord, timesheet.contract_record_id)
    assert contract is not None
    referred = await db.get(User, timesheet.user_id)
    job = await db.get(Job, contract.job_id)
    assert referred is not None and job is not None
    referrer = User(
        name="Referral Owner",
        username=f"ref{referred.id}"[:20],
        email=f"referrer.{referred.id}@example.com",
        hashed_password=get_password_hash("ReferralPass123!"),
        profile_image_url="https://example.com/referrer.png",
        data={},
    )
    db.add(referrer)
    await db.flush()
    referral = ReferralRecord(
        referrer_user_id=referrer.id,
        referred_user_id=referred.id,
        referred_talent_profile_id=timesheet.talent_profile_id,
        referrer_snapshot_name=referrer.name,
        referrer_snapshot_email=referrer.email,
        referred_snapshot_name=referred.name,
        referred_snapshot_email=referred.email,
        source_referral_code=f"REF-{referred.id}",
        referral_bonus_model_id=int(job.referral_bonus_model_id),
        model_snapshot_name="Referral settlement test",
        currency="USD",
        reward_cap=Decimal("50.00"),
        data={
            REFERRAL_BONUS_MILESTONES_DATA_KEY: [
                {"required_hours": "1.00", "reward_amount": "20.00"},
                {"required_hours": "2.00", "reward_amount": "40.00"},
            ]
        },
    )
    db.add(referral)
    await db.flush()
    return referral


async def test_reached_referral_milestones_materialize_capped_payables(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )

    await sync_settlement_month(db=db_session, settlement_month="2026-07")

    payables = list(
        (
            await db_session.scalars(
                select(Payable).where(Payable.payment_type == "referral_reward").order_by(Payable.source_key.asc())
            )
        ).all()
    )
    assert [item.source_key for item in payables] == [
        f"referral_reward:{referral.id}:0",
        f"referral_reward:{referral.id}:1",
    ]
    assert [item.amount for item in payables] == [Decimal("20.00"), Decimal("30.00")]
    assert all(item.user_id == referral.referrer_user_id for item in payables)
    assert all(item.referral_referred_user_id == referral.referred_user_id for item in payables)
    sources = list((await db_session.scalars(select(PayableTimesheetSource))).all())
    payable_ids = {item.id for item in payables}
    referral_source_ids = {source.payable_id for source in sources if source.payable_id in payable_ids}
    assert referral_source_ids == {item.id for item in payables}


async def test_referral_paid_and_reversed_totals_are_ledger_projections(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    await sync_settlement_month(db=db_session, settlement_month="2026-07")
    referral_payables = list(
        (
            await db_session.scalars(
                select(Payable).where(Payable.referral_record_id == referral.id).order_by(Payable.id.asc())
            )
        ).all()
    )
    initial = await list_referrals_for_admin(db=db_session)
    initial_item = initial["items"][0].children[0]
    assert initial_item.payable_reward_amount == Decimal("50.00")
    assert initial_item.paid_reward_amount == Decimal("0.00")
    assert initial_item.payout_status == "ready_to_pay"

    payable_ids = [item.id for item in referral_payables]
    await transition_payables(
        db=db_session,
        payable_ids=payable_ids,
        target=PayableStatus.PROCESSING,
        admin_user_id=int(superadmin_credentials["id"]),
    )
    payout = await pay_payables(
        db=db_session,
        payable_ids=payable_ids,
        details=PayoutDetails(
            external_platform="Wise",
            external_transaction_no="referral-ledger-payment",
            remark="Referral settlement test",
        ),
        admin_user_id=int(superadmin_credentials["id"]),
    )
    assert payout.failed_count == 0
    paid = await list_referrals_for_admin(db=db_session)
    paid_item = paid["items"][0].children[0]
    assert paid_item.payable_reward_amount == Decimal("0.00")
    assert paid_item.paid_reward_amount == Decimal("50.00")
    assert paid_item.payout_status == "paid"

    for index, result in enumerate(payout.items):
        assert result.payment is not None
        await reverse_payment(
            db=db_session,
            payment_id=result.payment.id,
            details=PayoutDetails(
                external_platform="Wise",
                external_transaction_no=f"referral-ledger-reversal-{index}",
                remark="Referral reversal test",
            ),
            admin_user_id=int(superadmin_credentials["id"]),
        )
    reversed_page = await list_referrals_for_admin(db=db_session)
    reversed_item = reversed_page["items"][0].children[0]
    assert reversed_item.payable_reward_amount == Decimal("0.00")
    assert reversed_item.paid_reward_amount == Decimal("0.00")
    assert reversed_item.payout_status == "reversed"
    assert reversed_item.last_paid_at is not None


async def test_admin_active_referral_count_excludes_inactive_referrals(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    contract = (
        await db_session.scalars(
            select(ContractRecord).where(ContractRecord.user_id == referral.referred_user_id)
        )
    ).one()
    contract.contract_status = "terminated"
    await db_session.flush()

    page = await list_referrals_for_admin(db=db_session)
    assert page["total"] == 1
    assert page["items"][0].children[0].status == "Inactive"
    assert page["items"][0].active_referral_count == 0
    assert page["summary"].active_referral_count == 0


async def test_admin_referrals_read_ledger_projection_and_old_mark_paid_is_removed(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    await db_session.commit()
    sync_response = await client.post(
        "/api/v1/payables/sync",
        headers=admin_auth_headers,
        json={"settlement_month": "2026-07"},
    )
    assert sync_response.status_code == 200, sync_response.text

    response = await client.get("/api/v1/referrals", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["payable_rewards"] == "50.00"
    child = payload["items"][0]["children"][0]
    assert child["id"] == referral.id
    assert child["paid_reward_amount"] == "0.00"
    assert child["payable_reward_amount"] == "50.00"
    assert child["payout_status"] == "ready_to_pay"

    removed_response = await client.post(
        f"/api/v1/referrals/{referral.id}/mark-paid",
        headers=admin_auth_headers,
    )
    assert removed_response.status_code == 404


async def test_candidate_referrals_read_the_same_payable_projection(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    referrer = await db_session.get(User, referral.referrer_user_id)
    referred_contract = (
        await db_session.scalars(select(ContractRecord).where(ContractRecord.user_id == referral.referred_user_id))
    ).one()
    job = await db_session.get(Job, referred_contract.job_id)
    assert referrer is not None and job is not None
    talent = TalentProfile(
        user_id=referrer.id,
        full_name=referrer.name,
        email=referrer.email,
        nationality="Brazil",
        location="Brazil",
        data={},
    )
    db_session.add(talent)
    await db_session.flush()
    application = CandidateApplication(
        user_id=referrer.id,
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
        user_id=referrer.id,
        application_id=application.id,
        talent_profile_id=talent.id,
        current_stage="active",
        screening_mode="manual",
        data={},
    )
    db_session.add(progress)
    await db_session.flush()
    referrer_contract = ContractRecord(
        user_id=referrer.id,
        user_snapshot_name=referrer.name,
        user_snapshot_email=referrer.email,
        talent_profile_id=talent.id,
        application_id=application.id,
        job_id=job.id,
        job_progress_id=progress.id,
        service_customer_company_id=referred_contract.service_customer_company_id,
        service_customer_project_id=referred_contract.service_customer_project_id,
        agreement_ref_no=f"REFERRER-{referrer.id}",
        contract_status="active",
        contract_type="normal",
        contractor_name=referrer.name,
        rate=Decimal("5.00"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date(2026, 7, 1),
        data={},
    )
    db_session.add(referrer_contract)
    await db_session.flush()
    await ensure_user_referral_profile_from_job(
        user_id=int(referrer.id),
        job=job,
        db=db_session,
        admin_user_id=int(superadmin_credentials["id"]),
        contract_record=referrer_contract,
    )
    await sync_settlement_month(db=db_session, settlement_month="2026-07")
    await db_session.commit()

    login_response = await web_client.post(
        "/api/v1/login",
        data={"username": referrer.username, "password": "ReferralPass123!"},
    )
    assert login_response.status_code == 200, login_response.text
    response = await web_client.get(
        "/api/v1/me/referrals",
        headers={"Authorization": f"Bearer {login_response.json()['access_token']}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["eligible"] is True
    assert payload["items"][0]["id"] == referral.id
    assert payload["items"][0]["payable_reward_amount"] == "50.00"
    assert payload["items"][0]["payout_status"] == "ready_to_pay"


async def test_earlier_timesheet_change_recalculates_later_pending_referral_milestone(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    referral = await _create_referral_with_reached_milestones(
        db_session,
        owner_admin_user_id=int(superadmin_credentials["id"]),
    )
    referral.data = {
        REFERRAL_BONUS_MILESTONES_DATA_KEY: [
            {"required_hours": "3.00", "reward_amount": "20.00"},
        ]
    }
    july = (
        await db_session.scalars(
            select(ProjectTimesheetRecord).where(ProjectTimesheetRecord.user_id == referral.referred_user_id)
        )
    ).one()
    august = ProjectTimesheetRecord(
        company_id=july.company_id,
        project_id=july.project_id,
        sub_project_name=july.sub_project_name,
        work_date=date(2026, 8, 2),
        user_id=july.user_id,
        talent_profile_id=july.talent_profile_id,
        contract_record_id=july.contract_record_id,
        user_name_snapshot=july.user_name_snapshot,
        user_email_snapshot=july.user_email_snapshot,
        language=july.language,
        work_type=july.work_type,
        candidate_duration_hours=Decimal("2.00"),
        customer_duration_hours=Decimal("2.00"),
        data={},
    )
    db_session.add(august)
    await db_session.flush()

    await sync_timesheet_change(
        db=db_session,
        settlement_month="2026-08",
        affected_user_ids=[referral.referred_user_id],
    )
    payable = (
        await db_session.scalars(
            select(Payable).where(
                Payable.referral_record_id == referral.id,
                Payable.payment_type == "referral_reward",
            )
        )
    ).one()
    assert payable.settlement_month == "2026-08"

    july.candidate_duration_hours = Decimal("0.50")
    await db_session.flush()
    result = await sync_timesheet_change(
        db=db_session,
        settlement_month="2026-07",
        affected_user_ids=[referral.referred_user_id],
    )
    assert result.settlement_month == "2026-07"
    referral_payables = (
        await db_session.scalars(
            select(Payable).where(
                Payable.referral_record_id == referral.id,
                Payable.payment_type == "referral_reward",
            )
        )
    ).all()
    assert referral_payables == []
