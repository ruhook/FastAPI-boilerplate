from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers.talent import build_form_fields, create_form_template, create_open_job


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_web_jobs_list_only_shows_open_jobs_and_supports_basic_filters(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"jobs-{suffix}", fields=fields)

    open_job = await create_open_job(
        db_session,
        suffix=f"jobs-open-{suffix}",
        title=f"Open Language Job {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    paused_job = await create_open_job(
        db_session,
        suffix=f"jobs-paused-{suffix}",
        title=f"Paused Language Job {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    paused_job.status = "暂停"
    paused_job.country = "Canada"
    paused_job.work_mode = "Onsite"
    paused_job.compensation_min = Decimal("6.00")
    paused_job.compensation_max = Decimal("9.00")
    await db_session.commit()

    list_response = await web_client.get("/api/v1/jobs")
    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    print(f"[web-jobs] list total={list_payload['total']} first_ids={[item['id'] for item in list_payload['items']]}")
    item_ids = [item["id"] for item in list_payload["items"]]
    assert open_job.id in item_ids
    assert paused_job.id not in item_ids

    keyword_response = await web_client.get("/api/v1/jobs", params={"keyword": open_job.title})
    assert keyword_response.status_code == 200, keyword_response.text
    keyword_payload = keyword_response.json()
    assert keyword_payload["total"] >= 1
    assert all(item["id"] == open_job.id for item in keyword_payload["items"])

    country_response = await web_client.get("/api/v1/jobs", params={"country": "Brazil"})
    assert country_response.status_code == 200, country_response.text
    assert open_job.id in [item["id"] for item in country_response.json()["items"]]

    work_mode_response = await web_client.get("/api/v1/jobs", params={"work_mode": "Remote"})
    assert work_mode_response.status_code == 200, work_mode_response.text
    assert open_job.id in [item["id"] for item in work_mode_response.json()["items"]]


async def test_web_job_detail_returns_public_fields_and_form_snapshot(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"detail-{suffix}", fields=fields)
    job = await create_open_job(
        db_session,
        suffix=f"detail-{suffix}",
        title=f"Detail Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )

    detail_response = await web_client.get(f"/api/v1/jobs/{job.id}")
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    print(
        f"[web-jobs] detail job={detail_payload['id']} form_template={detail_payload['form_template_id']} "
        f"fields={len(detail_payload['form_fields'])}"
    )
    assert detail_payload["id"] == job.id
    assert detail_payload["title"] == job.title
    assert detail_payload["status"] == "在招"
    assert detail_payload["form_template_id"] == template.id
    assert len(detail_payload["form_fields"]) == len(fields)
    assert detail_payload["form_fields"][0]["key"] == "full_name"
    assert detail_payload["description_html"]
    assert detail_payload["summary"]
    education_field = next(
        field for field in detail_payload["form_fields"] if field["key"] == "education_status"
    )
    assert education_field["options"]
