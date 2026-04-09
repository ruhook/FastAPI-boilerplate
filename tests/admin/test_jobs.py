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


async def test_superadmin_can_create_list_detail_and_update_job_with_company(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
) -> None:
    template = await create_form_template(
        db_session,
        suffix="job-company",
        fields=build_form_fields(),
    )

    create_response = await client.post(
        "/api/v1/jobs",
        headers=admin_auth_headers,
        json={
            "title": "Portuguese QA Reviewer",
            "company": "T-Maxx",
            "country": "Brazil",
            "status": "在招",
            "work_mode": "Remote",
            "compensation_min": "6",
            "compensation_max": "10",
            "compensation_unit": "Per Hour",
            "description": "<p>Review Portuguese content.</p>",
            "owner_name": "Super Admin",
            "collaborators": ["Ops A"],
            "form_strategy": {"template_id": template.id},
            "assessment_config": {
                "enabled": False,
                "mail_account_id": None,
                "mail_template_id": None,
                "mail_signature_id": None,
            },
            "form_fields": [
                {
                    "key": "full_name",
                    "label": "Full Name",
                    "type": "text",
                    "required": True,
                    "canFilter": True,
                }
            ],
            "automation_rules": {"combinator": "and", "rules": []},
            "screening_rules": [],
            "publish_checklist": ["已填写岗位基础信息"],
            "highlights": ["Brazil 岗位"],
            "application_summary": None,
        },
    )
    assert create_response.status_code == 201, create_response.text
    created_job = create_response.json()
    job_id = created_job["id"]
    assert created_job["company"] == "T-Maxx"

    list_response = await client.get("/api/v1/jobs", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    list_item = next(item for item in list_payload["items"] if item["id"] == job_id)
    assert list_item["company"] == "T-Maxx"

    detail_response = await client.get(f"/api/v1/jobs/{job_id}", headers=admin_auth_headers)
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    assert detail_payload["company"] == "T-Maxx"

    update_response = await client.patch(
        f"/api/v1/jobs/{job_id}",
        headers=admin_auth_headers,
        json={
            "company": "T-Maxx Updated",
            "description": "<p>Updated description.</p>",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_payload = update_response.json()
    assert updated_payload["company"] == "T-Maxx Updated"

    filtered_list_response = await client.get(
        "/api/v1/jobs",
        headers=admin_auth_headers,
        params={"company": "T-Maxx Updated"},
    )
    assert filtered_list_response.status_code == 200, filtered_list_response.text
    filtered_payload = filtered_list_response.json()
    assert any(item["id"] == job_id for item in filtered_payload["items"])


async def test_superadmin_can_read_job_progress_list(
    client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    template = await create_form_template(
        db_session,
        suffix="job-progress",
        fields=build_form_fields(),
    )
    job = await create_open_job(
        db_session,
        suffix="job-progress",
        title="Job Progress Admin Demo",
        company_name="Progress Ops",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=build_form_fields(),
        assessment_enabled=False,
    )
    user, password = await create_candidate_user(db_session, suffix="jobprogress", name="Progress Admin Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix="job-progress")

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Progress Admin Candidate",
                email=user.email,
                whatsapp="+55-5000-0000",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text

    progress_response = await client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
    )
    assert progress_response.status_code == 200, progress_response.text
    payload = progress_response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["job_id"] == job.id
    assert item["current_stage"] == "pending_screening"
    assert item["application_snapshot"]["full_name"] == "Progress Admin Candidate"
