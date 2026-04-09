from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers.admin import create_admin_user, create_role, login_admin_user
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


async def test_admin_without_talent_permission_cannot_access_talent_endpoints(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    role = await create_role(
        db_session,
        name=f"limited-{suffix}",
        permissions=["岗位管理"],
        description="missing talent permission",
    )
    limited_admin, limited_password = await create_admin_user(
        db_session,
        role_id=role.id,
        name="Limited Talent Viewer",
        username_prefix="limit",
    )
    limited_headers = await login_admin_user(
        admin_client,
        username_or_email=limited_admin.email,
        password=limited_password,
    )

    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"perm-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"perm-{suffix}",
        title=f"Permission Target Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    user, password = await create_candidate_user(db_session, suffix=f"perm{suffix}", name="Permission Candidate")
    web_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"perm-{suffix}", original_name="permission.pdf")

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=web_headers,
        json={
            "items": build_application_items(
                full_name="Permission Candidate",
                email=user.email,
                whatsapp="+1-202-555-0100",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    talent_id = apply_response.json()["talent_profile_id"]
    application_id = apply_response.json()["application_id"]

    list_response = await admin_client.get("/api/v1/talents", headers=limited_headers)
    assert list_response.status_code == 403, list_response.text
    assert "Missing admin permission: 总人才库" in list_response.json()["detail"]

    detail_response = await admin_client.get(f"/api/v1/talents/{talent_id}", headers=limited_headers)
    assert detail_response.status_code == 403, detail_response.text

    merge_response = await admin_client.post(
        f"/api/v1/talents/{talent_id}/merge-from-application/{application_id}",
        headers=limited_headers,
        json={"fields": ["whatsapp"]},
    )
    assert merge_response.status_code == 403, merge_response.text
