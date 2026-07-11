import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.const import ContractStatus
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job_progress.const import JobProgressDataKey, RecruitmentStage
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.referral.model import ReferralRecord
from src.app.modules.talent_profile.model import TalentProfile
from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    ensure_default_referral_bonus_model,
    login_web_user,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _build_talent_pool_form_fields() -> list[dict[str, object]]:
    fields = build_form_fields()
    for field in fields:
        if field.get("key") == "resume_attachment":
            field["type"] = "file"
    fields.extend(
        [
            {
                "key": "english_proficiency",
                "label": "English proficiency",
                "type": "single_select",
                "required": False,
                "canFilter": True,
            },
            {
                "key": "age_range",
                "label": "Age range",
                "type": "single_select",
                "required": False,
                "canFilter": True,
            },
            {
                "key": "native_languages",
                "label": "Native languages",
                "type": "text",
                "required": False,
                "canFilter": True,
            },
            {
                "key": "additional_languages",
                "label": "Additional languages",
                "type": "text",
                "required": False,
                "canFilter": True,
            },
        ]
    )
    return fields


async def _create_talent_with_sources(
    *,
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> dict[str, object]:
    suffix = uuid4().hex[:8]
    fields = _build_talent_pool_form_fields()
    template = await create_form_template(db_session, suffix=f"pool-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"pool-{suffix}",
        title=f"Talent Pool Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    user, password = await create_candidate_user(db_session, suffix=f"pool{suffix}", name="Talent Pool Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume", original_name="talent-resume.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"

    id_asset = await create_resume_asset(db_session, suffix=f"{suffix}-id", original_name="passport.pdf")
    id_asset.owner_id = user.id
    id_asset.module = "contract_record"
    user.data = {**(user.data or {}), "payment_info": {"id_attachment_asset_id": id_asset.id}}
    await db_session.commit()

    items = build_application_items(
        full_name="Talent Pool Candidate",
        email=user.email,
        whatsapp="+55-1000-2000",
        nationality="Brazilian",
        country_of_residence="Brazil",
        education_status="Bachelor completed",
        resume_asset_id=resume.id,
    )
    items.extend(
        [
            {
                "field_key": "english_proficiency",
                "value": "fully_professional_proficiency",
                "display_value": "Fully professional proficiency",
            },
            {"field_key": "age_range", "value": "26_30", "display_value": "26-30"},
            {"field_key": "native_languages", "value": "Portuguese"},
            {"field_key": "additional_languages", "value": "English"},
        ]
    )
    response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={"items": items},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    talent = (await db_session.execute(select(TalentProfile).where(TalentProfile.user_id == user.id))).scalar_one()
    progress = (
        await db_session.execute(select(JobProgress).where(JobProgress.application_id == payload["application_id"]))
    ).scalar_one()
    progress.current_stage = RecruitmentStage.ACTIVE.value
    progress.data = {
        **(progress.data or {}),
        JobProgressDataKey.JOB_LANGUAGES.value: "Portuguese",
        JobProgressDataKey.ONBOARDING_STATUS.value: "待入职材料",
        JobProgressDataKey.ONBOARDING_DATE.value: "2026-06-15",
        JobProgressDataKey.ACCEPTED_RATE.value: "8.50",
        JobProgressDataKey.CONTRACT_NUMBER.value: "PROG-001",
        JobProgressDataKey.NOTE.value: "Progress note",
    }

    referrer, _ = await create_candidate_user(db_session, suffix=f"ref{suffix}", name="Referral Owner")
    bonus_model = await ensure_default_referral_bonus_model(db_session)
    db_session.add(
        ReferralRecord(
            referrer_user_id=referrer.id,
            referred_user_id=user.id,
            referred_talent_profile_id=talent.id,
            referrer_snapshot_name="Referral Owner",
            referrer_snapshot_email=referrer.email,
            referred_snapshot_name=user.name,
            referred_snapshot_email=user.email,
            source_referral_code="RFTEST",
            referral_bonus_model_id=bonus_model.id,
            model_snapshot_name=bonus_model.name,
            currency=bonus_model.currency,
            reward_cap=bonus_model.reward_cap,
            data={},
        )
    )

    sealed = await create_resume_asset(db_session, suffix=f"{suffix}-sealed", original_name="sealed-contract.pdf")
    sealed.owner_id = user.id
    sealed.module = "contract_record"
    contract = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=talent.id,
        application_id=payload["application_id"],
        job_id=job.id,
        job_progress_id=progress.id,
        job_snapshot_title=job.title,
        service_customer_company_id=job.company_id,
        service_customer_project_id=job.project_id,
        agreement_ref_no="CON-009",
        contract_status="active",
        contract_type="normal",
        contractor_name=user.name,
        rate=Decimal("9.25"),
        effective_date=date(2026, 6, 16),
        end_date=date(2026, 12, 31),
        company_sealed_contract_asset_id=sealed.id,
        is_current=True,
        data={},
    )
    db_session.add(contract)
    db_session.add(
        ProjectTimesheetRecord(
            company_id=job.company_id,
            project_id=job.project_id,
            sub_project_name="Main",
            work_date=date(2026, 6, 20),
            user_id=user.id,
            talent_profile_id=talent.id,
            language="Portuguese",
            work_type="Production",
            candidate_duration_hours=Decimal("3.50"),
        )
    )
    db_session.add(
        ProjectTimesheetRecord(
            company_id=job.company_id,
            project_id=job.project_id,
            sub_project_name="Main",
            work_date=date(2026, 6, 21),
            user_id=user.id,
            talent_profile_id=talent.id,
            language="Portuguese",
            work_type="Production",
            candidate_duration_hours=Decimal("4.00"),
        )
    )
    await db_session.commit()

    return {"talent": talent, "progress": progress, "contract": contract, "user": user, "job": job}


async def test_admin_talent_pool_returns_aggregated_b_side_fields(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]

    response = await admin_client.get("/api/v1/talents", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["items"] if row["id"] == talent.id)

    assert item["english_proficiency"] == "Fully professional proficiency"
    assert item["age_range"] == "26-30"
    assert item["referrer_name"] == "Referral Owner"
    assert item["progress_language"] == "Portuguese"
    assert item["talent_status"] == "active"
    assert item["talent_status_label"] == "在职"
    assert item["talent_status_editable"] is True
    assert item["contract_type"] == "normal"
    assert item["accepted_hourly_rate"] == "9.25"
    assert item["contract_number"] == "CON-009"
    assert item["contract_effective_date"] == "2026-06-16"
    assert item["contract_end_date"] == "2026-12-31"
    assert item["resume_attachment_asset"]["name"] == "talent-resume.pdf"
    assert item["company_sealed_contract_asset"]["name"] == "sealed-contract.pdf"
    assert item["id_attachment_asset"]["name"] == "passport.pdf"
    assert item["onboarding_status"] == "待入职材料"
    assert item["onboarding_date"] == "2026-06-15"
    assert item["note"] == "Progress note"
    assert item["total_work_hours"] == "7.50"
    assert item["recent_work_date"] == "2026-06-21"


async def test_admin_talent_detail_includes_same_aggregated_fields(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]

    response = await admin_client.get(f"/api/v1/talents/{talent.id}", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    for key in [
        "english_proficiency",
        "age_range",
        "referrer_name",
        "progress_language",
        "talent_status",
        "company_sealed_contract_asset",
        "id_attachment_asset",
        "total_work_hours",
        "recent_work_date",
    ]:
        assert key in payload


async def test_talent_status_requires_onboarding_date_before_manual_edit(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]
    progress = context["progress"]
    progress.data = {**(progress.data or {}), JobProgressDataKey.ONBOARDING_DATE.value: None}
    await db_session.commit()

    response = await admin_client.patch(
        f"/api/v1/talents/{talent.id}/status",
        headers=admin_auth_headers,
        json={"status": "on_leave"},
    )
    assert response.status_code == 400
    assert "onboarding date" in response.text.lower()


async def test_talent_status_update_to_replaced_moves_progress_stage(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]
    progress = context["progress"]
    contract = context["contract"]

    response = await admin_client.patch(
        f"/api/v1/talents/{talent.id}/status",
        headers=admin_auth_headers,
        json={"status": "replaced"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["talent_status"] == "replaced"

    await db_session.refresh(progress)
    await db_session.refresh(contract)
    assert progress.current_stage == RecruitmentStage.REPLACED.value
    assert contract.contract_status == ContractStatus.TERMINATED.value


async def test_admin_contract_close_syncs_progress_and_terminal_contract_cannot_revive(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    progress = context["progress"]
    contract = context["contract"]

    close_response = await admin_client.patch(
        f"/api/v1/contracts/{contract.id}",
        headers=admin_auth_headers,
        json={"contract_status": ContractStatus.TERMINATED.value},
    )
    assert close_response.status_code == 200, close_response.text

    await db_session.refresh(progress)
    await db_session.refresh(contract)
    assert progress.current_stage == RecruitmentStage.REPLACED.value
    assert contract.contract_status == ContractStatus.TERMINATED.value

    revive_response = await admin_client.patch(
        f"/api/v1/contracts/{contract.id}",
        headers=admin_auth_headers,
        json={"contract_status": ContractStatus.PENDING_ACTIVATION.value},
    )
    assert revive_response.status_code == 409, revive_response.text


async def test_admin_can_join_existing_talent_to_another_job_once(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]
    talent_id = int(talent.id)
    source_job = context["job"]
    suffix = uuid4().hex[:8]
    target_job = await create_open_job(
        db_session,
        suffix=f"join-{suffix}",
        title=f"Talent Join Target {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=source_job.form_template_id,
        form_fields=_build_talent_pool_form_fields(),
        assessment_enabled=False,
    )

    response = await admin_client.post(
        f"/api/v1/talents/{talent_id}/join-job",
        headers=admin_auth_headers,
        json={"job_id": target_job.id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["talent_profile_id"] == talent_id
    assert payload["job_id"] == target_job.id
    assert payload["current_stage"] == RecruitmentStage.PENDING_SCREENING.value

    await db_session.rollback()
    application = await db_session.get(CandidateApplication, payload["application_id"])
    progress = await db_session.get(JobProgress, payload["job_progress_id"])
    await db_session.refresh(target_job)
    assert application is not None and application.data["source"] == "admin_talent_join"
    assert progress is not None and progress.talent_profile_id == talent_id
    assert target_job.applicant_count == 1

    duplicate_response = await admin_client.post(
        f"/api/v1/talents/{talent_id}/join-job",
        headers=admin_auth_headers,
        json={"job_id": target_job.id},
    )
    assert duplicate_response.status_code == 400, duplicate_response.text


async def test_talent_note_update_syncs_progress_and_profile_note(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]
    progress = context["progress"]

    response = await admin_client.patch(
        f"/api/v1/talents/{talent.id}/note",
        headers=admin_auth_headers,
        json={"note": "Updated pool note"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["note"] == "Updated pool note"

    await db_session.refresh(progress)
    await db_session.refresh(talent)
    assert progress.data[JobProgressDataKey.NOTE.value] == "Updated pool note"
    assert talent.note == "Updated pool note"


async def test_admin_talent_pool_keyword_search_matches_aggregated_sources(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]

    for keyword in ["Referral Owner", "CON-009", "待入职材料"]:
        response = await admin_client.get(
            "/api/v1/talents",
            headers=admin_auth_headers,
            params={"keyword": keyword},
        )
        assert response.status_code == 200, response.text
        assert any(row["id"] == talent.id for row in response.json()["items"])


async def test_admin_talent_pool_advanced_filter_matches_aggregated_fields(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    context = await _create_talent_with_sources(
        web_client=web_client,
        db_session=db_session,
        superadmin_credentials=superadmin_credentials,
    )
    talent = context["talent"]
    advanced_filter = {
        "combinator": "and",
        "rules": [
            {"field": "talent_status", "operator": "=", "value": "active"},
            {"field": "contract_type", "operator": "=", "value": "normal"},
        ],
    }

    response = await admin_client.get(
        "/api/v1/talents",
        headers=admin_auth_headers,
        params={"advanced_filter": json.dumps(advanced_filter)},
    )
    assert response.status_code == 200, response.text
    assert any(row["id"] == talent.id for row in response.json()["items"])
