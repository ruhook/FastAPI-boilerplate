from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.admin.dictionary.model import AdminDictionary
from src.app.modules.assets.model import Asset
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.job_progress.service import mark_job_progress_assessment_invited
from tests.helpers.talent import (
    build_application_items,
    build_automation_rules,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    login_web_user,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _build_web_application_fields() -> list[dict[str, object]]:
    fields = build_form_fields()
    for field in fields:
        if field.get("key") == "resume_attachment":
            field["type"] = "file"
    return fields


def _contract_asset(*, suffix: str, original_name: str, owner_id: int) -> Asset:
    return Asset(
        type="contract_file",
        module="contract",
        owner_type="user",
        owner_id=owner_id,
        original_name=original_name,
        storage_key=f"tests/contracts/{suffix}/{uuid4().hex}-{original_name}",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=256,
        data={},
    )


async def test_web_me_applications_returns_current_users_records(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = _build_web_application_fields()
    country_dictionary = (
        await db_session.execute(select(AdminDictionary).where(AdminDictionary.key == "country"))
    ).scalar_one_or_none()
    if country_dictionary is None:
        country_dictionary = AdminDictionary(
            key="country",
            label=f"Country Applications {suffix}",
            options=[{"label": "Brazil Label", "value": "Brazil"}],
            data={},
        )
        db_session.add(country_dictionary)
    else:
        country_dictionary.label = f"Country Applications {suffix}"
        country_dictionary.options = [{"label": "Brazil Label", "value": "Brazil"}]
    await db_session.commit()
    template = await create_form_template(db_session, suffix=f"me-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"me-{suffix}",
        title=f"My Jobs Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )

    user, password = await create_candidate_user(db_session, suffix=f"me{suffix}", name="My Jobs Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"me-{suffix}", original_name="me.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()
    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="My Jobs Candidate",
                email=user.email,
                whatsapp="+1-333-3333",
                nationality="Chinese",
                country_of_residence="China",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    application_id = apply_response.json()["application_id"]

    list_response = await web_client.get("/api/v1/me/applications", headers=auth_headers)
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()
    assert payload["total"] >= 1
    item = next((row for row in payload["items"] if row["application_id"] == application_id), None)
    assert item is not None
    assert item["job_id"] == job.id
    assert item["job_title"] == job.title
    assert item["current_stage"]
    assert item["country"] == "Brazil"
    assert item["country_label"] == "Brazil Label"
    assert item["work_mode"] == job.work_mode
    assert item["candidate_status"] == "under_review"
    assert item["candidate_stage"] == "application_review"
    assert item["candidate_action"] == "view_details"
    assert item["candidate_action_required"] is False
    assert item["candidate_status_label"] == "Under Review"
    assert item["candidate_stage_title"] == "Application Review"
    assert item["candidate_action_label"] == "View Details"
    assert payload["summary"] == {
        "contract_uploads": 0,
        "other_actions": 0,
        "monitoring": 1,
        "total_action_required": 0,
    }

    detail_response = await web_client.get(
        f"/api/v1/me/applications/{application_id}",
        headers=auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    assert detail_payload["application_id"] == application_id
    assert detail_payload["job_id"] == job.id
    assert detail_payload["job_title"] == job.title
    assert detail_payload["country_label"] == "Brazil Label"
    assert detail_payload["candidate_status"] == "under_review"
    assert detail_payload["candidate_stage"] == "application_review"
    assert detail_payload["candidate_action"] == "view_details"
    assert detail_payload["show_compensation"] is True
    assert detail_payload["description_html"]


async def test_web_me_applications_shows_review_until_assessment_invite(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=f"visible-stage-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"visible-stage-{suffix}",
        title=f"Visible Stage Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="education_status",
            operator="contains",
            value="PhD",
        ),
    )

    user, password = await create_candidate_user(
        db_session,
        suffix=f"visiblestage{suffix}",
        name="Visible Stage Candidate",
    )
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"visible-stage-{suffix}", original_name="visible.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Visible Stage Candidate",
                email=user.email,
                whatsapp="+55-8000-0000",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    application_id = int(apply_response.json()["application_id"])

    list_response = await web_client.get("/api/v1/me/applications", headers=auth_headers)
    assert list_response.status_code == 200, list_response.text
    item = next(row for row in list_response.json()["items"] if row["application_id"] == application_id)
    assert item["current_stage"] == "pending_screening"
    assert item["candidate_visible_stage"] == "review"
    assert item["candidate_visible_stage_label"] == "Review"

    detail_response = await web_client.get(
        f"/api/v1/me/applications/{application_id}",
        headers=auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    assert detail_payload["current_stage"] == "pending_screening"
    assert detail_payload["candidate_visible_stage"] == "review"
    assert detail_payload["candidate_visible_stage_label"] == "Review"

    async with local_session() as invite_session:
        progress_result = await invite_session.execute(
            select(JobProgress).where(JobProgress.application_id == application_id)
        )
        progress = progress_result.scalar_one()
        await mark_job_progress_assessment_invited(
            job_id=job.id,
            progress_ids=[progress.id],
            admin_user_id=int(superadmin_credentials["id"]),
            db=invite_session,
            sent_at=datetime.now(UTC),
        )
        await invite_session.commit()

    invited_response = await web_client.get(
        f"/api/v1/me/applications/{application_id}",
        headers=auth_headers,
    )
    assert invited_response.status_code == 200, invited_response.text
    invited_payload = invited_response.json()
    assert invited_payload["current_stage"] == "pending_screening"
    assert invited_payload["candidate_visible_stage"] == "assessment_file"
    assert invited_payload["candidate_visible_stage_label"] == "Assessment File"
    assert invited_payload["candidate_status"] == "action_required"
    assert invited_payload["candidate_stage"] == "assessment_file"
    assert invited_payload["candidate_action"] == "upload_assessment"


async def test_web_me_applications_summary_and_action_filter_use_full_presentation_set(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=f"summary-{suffix}", fields=fields)
    user, password = await create_candidate_user(
        db_session,
        suffix=f"summary{suffix}",
        name="Application Summary Candidate",
    )
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"summary-{suffix}", original_name="summary.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    definitions = [
        ("monitoring", False),
        ("assessment_action", True),
        ("rate_action", False),
        ("contract_action", False),
    ]
    application_ids: dict[str, int] = {}
    job_snapshots: dict[str, dict[str, object]] = {}
    for key, assessment_enabled in definitions:
        job = await create_open_job(
            db_session,
            suffix=f"summary-{key}-{suffix}",
            title=f"Summary {key} {suffix}",
            owner_admin_user_id=int(superadmin_credentials["id"]),
            form_template_id=template.id,
            form_fields=fields,
            assessment_enabled=assessment_enabled,
        )
        job_snapshots[key] = {
            "title": job.title,
            "company_id": job.company_id,
            "project_id": job.project_id,
        }
        apply_response = await web_client.post(
            f"/api/v1/jobs/{job.id}/apply",
            headers=auth_headers,
            json={
                "items": build_application_items(
                    full_name="Application Summary Candidate",
                    email=user.email,
                    whatsapp="+55-8111-1111",
                    nationality="Brazilian",
                    country_of_residence="Brazil",
                    education_status="Bachelor’s degree (completed)",
                    resume_asset_id=resume.id,
                )
            },
        )
        assert apply_response.status_code == 200, apply_response.text
        application_ids[key] = int(apply_response.json()["application_id"])

    user_id = int(user.id)
    user_name = str(user.name)
    user_email = str(user.email)
    await db_session.rollback()
    progress_result = await db_session.execute(
        select(JobProgress).where(JobProgress.application_id.in_(application_ids.values()))
    )
    progress_by_application_id = {
        int(progress.application_id): progress for progress in progress_result.scalars().all()
    }

    assessment_progress = progress_by_application_id[application_ids["assessment_action"]]
    assessment_progress.data = {"assessment_sent_at": "2026-07-10T08:00:00Z"}

    rate_progress = progress_by_application_id[application_ids["rate_action"]]
    rate_progress.current_stage = "screening_passed"
    rate_progress.data = {"onboarding_status": "已发砍价"}

    contract_progress = progress_by_application_id[application_ids["contract_action"]]
    contract_progress.current_stage = "contract_pool"
    draft_asset = _contract_asset(suffix=suffix, original_name="summary-draft.docx", owner_id=user_id)
    db_session.add(draft_asset)
    await db_session.flush()
    contract_job = job_snapshots["contract_action"]
    db_session.add(
        ContractRecord(
            user_id=user_id,
            user_snapshot_name=user_name,
            user_snapshot_email=user_email,
            talent_profile_id=contract_progress.talent_profile_id,
            application_id=application_ids["contract_action"],
            job_id=contract_progress.job_id,
            job_progress_id=contract_progress.id,
            job_snapshot_title=str(contract_job["title"]),
            service_customer_company_id=int(contract_job["company_id"]),
            service_customer_project_id=int(contract_job["project_id"]),
            agreement_ref_no=f"SUMMARY-{suffix}",
            contract_status="Pending Signature",
            contractor_name=user_name,
            draft_contract_asset_id=draft_asset.id,
            data={},
        )
    )
    await db_session.commit()

    list_response = await web_client.get(
        "/api/v1/me/applications?page=1&page_size=2",
        headers=auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()
    assert payload["total"] == 4
    assert len(payload["items"]) == 2
    assert payload["summary"] == {
        "contract_uploads": 1,
        "other_actions": 2,
        "monitoring": 1,
        "total_action_required": 3,
    }

    action_response = await web_client.get(
        "/api/v1/me/applications?needs_action_only=true&page_size=10",
        headers=auth_headers,
    )
    assert action_response.status_code == 200, action_response.text
    action_payload = action_response.json()
    assert action_payload["total"] == 3
    assert {int(item["application_id"]) for item in action_payload["items"]} == {
        application_ids["assessment_action"],
        application_ids["rate_action"],
        application_ids["contract_action"],
    }
    assert all(item["candidate_action_required"] is True for item in action_payload["items"])
    assert action_payload["summary"] == {
        "contract_uploads": 1,
        "other_actions": 2,
        "monitoring": 0,
        "total_action_required": 3,
    }


async def test_web_me_contracts_only_lists_company_signed_active_contracts(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=f"contracts-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"contracts-{suffix}",
        title=f"My Contracts Active Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )

    user, password = await create_candidate_user(
        db_session,
        suffix=f"contracts{suffix}",
        name="My Contracts Candidate",
    )
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"contracts-{suffix}", original_name="contracts.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="My Contracts Candidate",
                email=user.email,
                whatsapp="+1-444-4444",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    application_id = int(apply_response.json()["application_id"])

    progress_result = await db_session.execute(select(JobProgress).where(JobProgress.application_id == application_id))
    progress = progress_result.scalar_one()
    progress.current_stage = "contract_pool"

    draft_asset = _contract_asset(suffix=suffix, original_name="draft-contract.docx", owner_id=user.id)
    signed_asset = _contract_asset(suffix=suffix, original_name="candidate-signed.docx", owner_id=user.id)
    sealed_asset = _contract_asset(suffix=suffix, original_name="company-signed.docx", owner_id=user.id)
    db_session.add_all([draft_asset, signed_asset, sealed_asset])
    await db_session.flush()

    contract = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=progress.talent_profile_id,
        application_id=application_id,
        job_id=job.id,
        job_progress_id=progress.id,
        job_snapshot_title=job.title,
        service_customer_project_id=job.project_id,
        agreement_ref_no=f"ACTIVE-ONLY-{suffix}",
        contract_status="Pending Activation",
        contractor_name=user.name,
        draft_contract_asset_id=draft_asset.id,
        candidate_signed_contract_asset_id=signed_asset.id,
        data={},
    )
    db_session.add(contract)
    await db_session.commit()

    pending_response = await web_client.get("/api/v1/me/contracts", headers=auth_headers)
    assert pending_response.status_code == 200, pending_response.text
    assert all(int(item["application_id"]) != application_id for item in pending_response.json()["items"])

    contract.company_sealed_contract_asset_id = sealed_asset.id
    contract.contract_status = "Active"
    progress.current_stage = "active"
    await db_session.commit()

    active_response = await web_client.get("/api/v1/me/contracts", headers=auth_headers)
    assert active_response.status_code == 200, active_response.text
    active_item = next(
        (item for item in active_response.json()["items"] if int(item["application_id"]) == application_id),
        None,
    )
    assert active_item is not None
    assert active_item["contract_record_data"]["company_sealed_contract_attachment"]["name"] == "company-signed.docx"
