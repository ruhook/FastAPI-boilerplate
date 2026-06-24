from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.admin.dictionary.model import AdminDictionary
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
