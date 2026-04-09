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


async def test_admin_cannot_merge_application_from_another_talent_profile(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"merge-error-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"merge-error-{suffix}",
        title=f"Merge Error Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )

    first_user, first_password = await create_candidate_user(db_session, suffix=f"mergea{suffix}", name="First Talent")
    second_user, second_password = await create_candidate_user(db_session, suffix=f"mergeb{suffix}", name="Second Talent")

    first_headers = await login_web_user(web_client, username=first_user.email, password=first_password)
    second_headers = await login_web_user(web_client, username=second_user.email, password=second_password)
    first_resume = await create_resume_asset(db_session, suffix=f"merge-a-{suffix}", original_name="first.pdf")
    second_resume = await create_resume_asset(db_session, suffix=f"merge-b-{suffix}", original_name="second.pdf")

    first_apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=first_headers,
        json={
            "items": build_application_items(
                full_name="First Talent",
                email=first_user.email,
                whatsapp="+1-111-1111",
                nationality="Chinese",
                country_of_residence="China",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=first_resume.id,
            )
        },
    )
    assert first_apply_response.status_code == 200, first_apply_response.text
    first_talent_id = first_apply_response.json()["talent_profile_id"]

    second_apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=second_headers,
        json={
            "items": build_application_items(
                full_name="Second Talent",
                email=second_user.email,
                whatsapp="+1-222-2222",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Master’s degree (completed)",
                resume_asset_id=second_resume.id,
            )
        },
    )
    assert second_apply_response.status_code == 200, second_apply_response.text
    second_application_id = second_apply_response.json()["application_id"]

    merge_response = await admin_client.post(
        f"/api/v1/talents/{first_talent_id}/merge-from-application/{second_application_id}",
        headers=admin_auth_headers,
        json={"fields": ["whatsapp", "education_status"]},
    )
    assert merge_response.status_code == 400, merge_response.text
    assert "Application does not belong to this talent profile." in merge_response.json()["detail"]
