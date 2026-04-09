from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.app.core.db.database import local_session
from src.app.modules.assets.model import Asset
from src.app.modules.job_progress.model import JobProgress

from tests.helpers.talent import (
    build_application_items,
    build_automation_rules,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    fetch_operation_logs,
    login_web_user,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_web_candidate_can_upload_assessment_attachment(
    web_client: AsyncClient,
    db_session,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Assessment Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="country_of_residence",
            operator="contains",
            value="Brazil",
        ),
    )
    user, password = await create_candidate_user(db_session, suffix=suffix, name="Assessment Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(
        db_session,
        suffix=f"{suffix}-resume",
        original_name="assessment-resume.pdf",
    )

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Assessment Candidate",
                email=user.email,
                whatsapp="+55-9999-9999",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    application_payload = apply_response.json()

    upload_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/assessment/upload",
        headers=auth_headers,
        files={
            "file": (
                "assessment-answer.pdf",
                b"%PDF-1.4\n% demo assessment\n",
                "application/pdf",
            )
        },
    )
    assert upload_response.status_code == 201, upload_response.text
    upload_payload = upload_response.json()

    assert upload_payload["job_progress_id"] > 0
    assert upload_payload["job_id"] == job.id
    assert upload_payload["application_id"] == application_payload["application_id"]
    assert upload_payload["current_stage"] == "assessment_review"
    assert upload_payload["assessment_asset"]["original_name"] == "assessment-answer.pdf"
    assert upload_payload["process_data"]["assessment_attachment"] == "assessment-answer.pdf"
    assert upload_payload["process_data"]["assessment_attachment_asset_id"] == upload_payload["assessment_asset"]["id"]
    assert upload_payload["process_assets"]["assessment_attachment"]["asset_id"] == upload_payload["assessment_asset"]["id"]

    asset_read_response = await web_client.get(
        f"/api/v1/assets/{upload_payload['assessment_asset']['id']}",
        headers=auth_headers,
    )
    assert asset_read_response.status_code == 200, asset_read_response.text
    assert asset_read_response.json()["original_name"] == "assessment-answer.pdf"

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == application_payload["application_id"])
        )
        progress = progress_result.scalar_one()
        assert progress.current_stage == "assessment_review"
        assert progress.data["assessment_attachment"] == "assessment-answer.pdf"
        assert progress.data["assessment_attachment_asset_id"] == upload_payload["assessment_asset"]["id"]
        assert progress.data["assessment_submitted_at"]

        asset_result = await assertion_session.execute(
            select(Asset).where(Asset.id == upload_payload["assessment_asset"]["id"])
        )
        asset = asset_result.scalar_one()
        assert asset.owner_type == "user"
        assert asset.owner_id == user.id
        assert asset.module == "job_progress"

        operation_logs = await fetch_operation_logs(assertion_session, user_id=user.id)
        log_types = [log.log_type for log in operation_logs]
        assert log_types == [
            "candidate_application_submitted",
            "talent_profile_initial_auto_merge",
            "job_progress_created",
            "job_progress_stage_changed",
            "job_progress_assessment_submitted",
        ]
