from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.talent_profile_merge_log.model import TalentProfileMergeLog

from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    fetch_operation_logs,
    login_web_user,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_can_list_detail_and_manually_merge_talent_from_second_application(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=f"admin-{suffix}", fields=fields)
    first_job = await create_open_job(
        db_session,
        suffix=f"admin-{suffix}-1",
        title=f"Initial Annotation Role {suffix}",
        company_name=f"Initial Company {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    second_job = await create_open_job(
        db_session,
        suffix=f"admin-{suffix}-2",
        title=f"Promotion Review Role {suffix}",
        company_name=f"Updated Company {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    user, password = await create_candidate_user(db_session, suffix=f"admin{suffix}", name="Bob Snapshot")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    first_resume = await create_resume_asset(db_session, suffix=f"{suffix}-first", original_name="bob-first.pdf")
    second_resume = await create_resume_asset(db_session, suffix=f"{suffix}-second", original_name="bob-second.pdf")

    first_apply_response = await web_client.post(
        f"/api/v1/jobs/{first_job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Bob Snapshot",
                email=user.email,
                whatsapp="+86-1000-0001",
                nationality="Chinese",
                country_of_residence="China",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=first_resume.id,
            )
        },
    )
    assert first_apply_response.status_code == 200, first_apply_response.text
    first_apply = first_apply_response.json()

    second_apply_response = await web_client.post(
        f"/api/v1/jobs/{second_job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Bob Snapshot Updated",
                email=user.email,
                whatsapp="+86-2000-0002",
                nationality="Chinese",
                country_of_residence="Singapore",
                education_status="Master’s degree (completed)",
                resume_asset_id=second_resume.id,
            )
        },
    )
    assert second_apply_response.status_code == 200, second_apply_response.text
    second_apply = second_apply_response.json()
    talent_id = first_apply["talent_profile_id"]

    print(
        f"[talent-admin] created talent={talent_id}, first_application={first_apply['application_id']}, "
        f"second_application={second_apply['application_id']}"
    )

    list_response = await admin_client.get("/api/v1/talents", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    assert list_payload["total"] >= 1
    talent_list_item = next(item for item in list_payload["items"] if item["id"] == talent_id)
    assert talent_list_item["full_name"] == "Bob Snapshot"
    assert talent_list_item["education"] == "Bachelor’s degree (completed)"
    assert talent_list_item["latest_applied_job_title"] == second_job.title

    detail_response = await admin_client.get(f"/api/v1/talents/{talent_id}", headers=admin_auth_headers)
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    print(f"[talent-admin] detail before merge: {detail_payload}")
    assert detail_payload["full_name"] == "Bob Snapshot"
    assert detail_payload["whatsapp"] == "+86-1000-0001"
    assert detail_payload["education"] == "Bachelor’s degree (completed)"
    assert detail_payload["resume_asset_id"] == first_resume.id
    assert len(detail_payload["applications"]) == 2
    assert next(item for item in detail_payload["applications"] if item["id"] == first_apply["application_id"])[
        "job_snapshot_company_name"
    ] == first_job.company_name
    assert next(item for item in detail_payload["applications"] if item["id"] == second_apply["application_id"])[
        "job_snapshot_company_name"
    ] == second_job.company_name
    assert len(detail_payload["logs"]) >= 4
    assert detail_payload["logs"][0]["title"]
    assert detail_payload["logs"][0]["summary"]
    assert sum(1 for item in detail_payload["applications"] if item["source_of_current_snapshot"]) == 1
    assert next(item for item in detail_payload["applications"] if item["source_of_current_snapshot"])["id"] == first_apply[
        "application_id"
    ]

    merge_response = await admin_client.post(
        f"/api/v1/talents/{talent_id}/merge-from-application/{second_apply['application_id']}",
        headers=admin_auth_headers,
        json={"fields": ["whatsapp", "education_status", "resume_attachment"]},
    )
    assert merge_response.status_code == 200, merge_response.text
    merged_payload = merge_response.json()
    print(f"[talent-admin] detail after merge: {merged_payload}")
    assert merged_payload["full_name"] == "Bob Snapshot"
    assert merged_payload["whatsapp"] == "+86-2000-0002"
    assert merged_payload["education"] == "Master’s degree (completed)"
    assert merged_payload["resume_asset_id"] == second_resume.id
    assert merged_payload["source_application_id"] == second_apply["application_id"]
    assert merged_payload["merge_strategy"] == "manual_merge"
    assert sum(1 for item in merged_payload["applications"] if item["source_of_current_snapshot"]) == 1
    assert next(item for item in merged_payload["applications"] if item["source_of_current_snapshot"])["id"] == second_apply[
        "application_id"
    ]

    async with local_session() as assertion_session:
        talent_result = await assertion_session.execute(select(TalentProfile).where(TalentProfile.id == talent_id))
        talent = talent_result.scalar_one()
        assert talent.full_name == "Bob Snapshot"
        assert talent.whatsapp == "+86-2000-0002"
        assert talent.education == "Master’s degree (completed)"
        assert talent.resume_asset_id == second_resume.id
        assert talent.latest_applied_job_id == second_job.id
        assert talent.source_application_id == second_apply["application_id"]

        merge_logs_result = await assertion_session.execute(
            select(TalentProfileMergeLog)
            .where(TalentProfileMergeLog.talent_profile_id == talent_id)
            .order_by(TalentProfileMergeLog.id.asc())
        )
        merge_logs = list(merge_logs_result.scalars().all())
        assert len(merge_logs) == 2
        assert [row.merge_strategy for row in merge_logs] == ["initial_auto_merge", "manual_merge"]

        operation_logs = await fetch_operation_logs(assertion_session, user_id=user.id)
        log_types = [log.log_type for log in operation_logs]
        print(f"[talent-admin] operation logs: {log_types}")
        assert log_types == [
            "candidate_application_submitted",
            "talent_profile_initial_auto_merge",
            "job_progress_created",
            "candidate_application_submitted",
            "talent_profile_latest_application_updated",
            "job_progress_created",
            "talent_profile_manual_merge",
        ]
