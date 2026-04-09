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


async def test_web_apply_requires_authenticated_user(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"anon-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"anon-{suffix}",
        title=f"Anonymous Apply Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    resume = await create_resume_asset(db_session, suffix=f"anon-{suffix}", original_name="anon.pdf")

    response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        json={
            "items": build_application_items(
                full_name="Anonymous",
                email="anonymous@example.com",
                whatsapp="+1-000-0000",
                nationality="Chinese",
                country_of_residence="China",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert response.status_code == 401, response.text


async def test_web_apply_rejects_non_open_job(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"closed-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"closed-{suffix}",
        title=f"Closed Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    job.status = "暂停"
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=f"closed{suffix}", name="Closed Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"closed-{suffix}", original_name="closed.pdf")

    response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Closed Candidate",
                email=user.email,
                whatsapp="+1-999-9999",
                nationality="Chinese",
                country_of_residence="China",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert response.status_code == 404, response.text
    assert "Job not found." in response.json()["detail"]


async def test_web_apply_rejects_duplicate_application_for_same_job(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"dup-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"dup-{suffix}",
        title=f"Duplicate Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )

    user, password = await create_candidate_user(db_session, suffix=f"dup{suffix}", name="Duplicate Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"dup-{suffix}", original_name="duplicate.pdf")
    payload = {
        "items": build_application_items(
            full_name="Duplicate Candidate",
            email=user.email,
            whatsapp="+1-222-2222",
            nationality="Chinese",
            country_of_residence="China",
            education_status="Bachelor’s degree (completed)",
            resume_asset_id=resume.id,
        )
    }

    first_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json=payload,
    )
    assert first_response.status_code == 200, first_response.text

    duplicate_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json=payload,
    )
    assert duplicate_response.status_code == 400, duplicate_response.text
    assert "already applied" in duplicate_response.json()["detail"]
