from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
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


async def _submit_application_for_progress(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
    *,
    assessment_enabled: bool,
    automation_rules: dict[str, object],
    education_status: str,
) -> tuple[dict[str, object], JobProgress, list[str]]:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Progress Role {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=assessment_enabled,
        automation_rules=automation_rules,
    )
    user, password = await create_candidate_user(db_session, suffix=suffix, name="Progress Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")

    response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Progress Candidate",
                email=user.email,
                whatsapp="+55-3000-0000",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status=education_status,
                resume_asset_id=resume.id,
            )
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == payload["application_id"])
        )
        progress = progress_result.scalar_one()
        operation_logs = await fetch_operation_logs(assertion_session, user_id=user.id)
        log_types = [log.log_type for log in operation_logs]

    return payload, progress, log_types


async def test_job_progress_auto_screening_pass_with_assessment(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, log_types = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="education_status",
            operator="contains",
            value="Bachelor",
        ),
        education_status="Bachelor’s degree (completed)",
    )

    assert progress.current_stage == "assessment_review"
    assert progress.screening_mode == "auto"
    assert log_types[-2:] == ["job_progress_created", "job_progress_stage_changed"]


async def test_job_progress_auto_screening_pass_without_assessment(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, log_types = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=False,
        automation_rules=build_automation_rules(
            field_key="nationality",
            operator="contains",
            value="Brazil",
        ),
        education_status="Bachelor’s degree (completed)",
    )

    assert progress.current_stage == "screening_passed"
    assert progress.screening_mode == "auto"
    assert log_types[-2:] == ["job_progress_created", "job_progress_stage_changed"]


async def test_job_progress_auto_screening_fail_moves_to_rejected(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, log_types = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="education_status",
            operator="contains",
            value="PhD",
        ),
        education_status="Bachelor’s degree (completed)",
    )

    assert progress.current_stage == "rejected"
    assert progress.screening_mode == "auto"
    assert log_types[-2:] == ["job_progress_created", "job_progress_stage_changed"]
