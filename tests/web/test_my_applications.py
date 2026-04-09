from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    login_web_user,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_web_me_applications_returns_current_users_records(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
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
    assert detail_payload["description_html"]
