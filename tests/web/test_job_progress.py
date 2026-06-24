import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_task.const import MAIL_TASK_DATA_RENDER_CONTEXT_KEY, MailTaskStatus
from src.app.modules.admin.mail_task.model import MailTask
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.job_progress.service import sync_assessment_sent_at_from_mail_task
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


def _build_web_application_fields() -> list[dict[str, object]]:
    fields = build_form_fields()
    for field in fields:
        if field.get("key") == "resume_attachment":
            field["type"] = "file"
    return fields


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
    fields = _build_web_application_fields()
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
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

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


async def test_job_progress_auto_screening_pass_with_assessment_stays_pending_screening(
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

    assert progress.current_stage == "pending_screening"
    assert progress.screening_mode == "auto"
    assert "job_progress_created" in log_types
    assert "job_progress_stage_changed" not in log_types


async def test_job_progress_auto_screening_pass_without_assessment_stays_pending_screening(
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

    assert progress.current_stage == "pending_screening"
    assert progress.screening_mode == "auto"
    assert "job_progress_created" in log_types
    assert "job_progress_stage_changed" not in log_types


async def test_job_progress_auto_screening_fail_stays_pending_screening(
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

    assert progress.current_stage == "pending_screening"
    assert progress.screening_mode == "auto"
    assert "job_progress_created" in log_types
    assert "job_progress_stage_changed" not in log_types


async def test_admin_marks_assessment_sent_time_without_moving_stage(
    admin_client: AsyncClient,
    admin_auth_headers: dict[str, str],
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )

    response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/assessment-invite",
        headers=admin_auth_headers,
        json={
            "progress_ids": [progress.id],
            "sent_at": "2026-06-24T08:30:00+00:00",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 1
    assert "assessment_sent_at" in response.json()["updated_field_keys"]

    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.current_stage == "pending_screening"
        assert saved.data["assessment_sent_at"] == "2026-06-24T08:30:00+00:00"
        assert saved.data["assessment_invited_at"] == "2026-06-24T08:30:00+00:00"

    list_response = await admin_client.get(
        f"/api/v1/jobs/{progress.job_id}/progress",
        headers=admin_auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    item = next(row for row in list_response.json()["items"] if row["id"] == progress.id)
    assert item["process_data"]["assessment_sent_at"] == "2026-06-24T08:30:00+00:00"

    filter_response = await admin_client.get(
        f"/api/v1/jobs/{progress.job_id}/progress",
        headers=admin_auth_headers,
        params={
            "active_stage": "screening",
            "advanced_filter": json.dumps(
                {
                    "combinator": "and",
                    "rules": [
                        {
                            "field": "assessment_sent_at",
                            "operator": "=",
                            "value": "2026-06-24",
                        }
                    ],
                }
            ),
        },
    )
    assert filter_response.status_code == 200, filter_response.text
    assert progress.id in filter_response.json()["matched_progress_ids"]


async def test_moving_back_to_screening_preserves_assessment_send_time(
    admin_client: AsyncClient,
    admin_auth_headers: dict[str, str],
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="education_status",
            operator="contains",
            value="PhD",
        ),
        education_status="Bachelor's degree (completed)",
    )
    assert progress.current_stage == "pending_screening"

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={
            "progress_ids": [progress.id],
            "target_stage": "rejected",
            "reason": "set up return-to-screening preservation regression",
        },
    )
    assert reject_response.status_code == 200, reject_response.text

    async with local_session() as setup_session:
        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.current_stage == "rejected"
        saved.data = {
            **(saved.data or {}),
            "assessment_invited_at": "2026-06-24T08:00:00+00:00",
            "assessment_invite_mail_task_id": 123,
            "assessment_sent_at": "2026-06-24T08:30:00+00:00",
        }
        await setup_session.commit()

    response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={
            "progress_ids": [progress.id],
            "target_stage": "pending_screening",
            "reason": "preserve assessment send time regression",
        },
    )

    assert response.status_code == 200, response.text

    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.current_stage == "pending_screening"
        assert saved.data["assessment_sent_at"] == "2026-06-24T08:30:00+00:00"
        assert saved.data["assessment_invited_at"] == "2026-06-24T08:00:00+00:00"
        assert saved.data["assessment_invite_mail_task_id"] == 123


async def test_rejected_from_stage_uses_actual_source_stage_for_filtering(
    admin_client: AsyncClient,
    admin_auth_headers: dict[str, str],
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )
    assert progress.current_stage == "pending_screening"

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={
            "progress_ids": [progress.id],
            "target_stage": "rejected",
            "reason": "reject from pending screening regression",
        },
    )
    assert reject_response.status_code == 200, reject_response.text

    list_response = await admin_client.get(
        f"/api/v1/jobs/{progress.job_id}/progress",
        headers=admin_auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    item = next(row for row in list_response.json()["items"] if row["id"] == progress.id)
    assert item["process_data"]["rejected_from_stage"] == "pending_screening"

    filter_response = await admin_client.get(
        f"/api/v1/jobs/{progress.job_id}/progress",
        headers=admin_auth_headers,
        params={
            "active_stage": "eliminated",
            "advanced_filter": json.dumps(
                {
                    "combinator": "and",
                    "rules": [
                        {
                            "field": "rejected_from_stage",
                            "operator": "=",
                            "value": "screening",
                        }
                    ],
                }
            ),
        },
    )
    assert filter_response.status_code == 200, filter_response.text
    assert progress.id in filter_response.json()["matched_progress_ids"]


async def test_successful_assessment_mail_task_syncs_sent_at_to_progress(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )
    sent_at = datetime(2026, 6, 24, 8, 45, tzinfo=UTC)

    async with local_session() as setup_session:
        account = MailAccount(
            admin_user_id=int(superadmin_credentials["id"]),
            email="assessment-sync@example.com",
            provider="qq",
            smtp_username="assessment-sync@example.com",
            smtp_host="smtp.qq.com",
            smtp_port=587,
            security_mode="starttls",
            auth_secret="smtp-auth-code",
            status="enabled",
            data={},
        )
        setup_session.add(account)
        await setup_session.flush()
        task = MailTask(
            account_id=account.id,
            template_id=None,
            signature_id=None,
            subject="Assessment",
            body_html="<p>Assessment</p>",
            to_recipients=[{"name": "Progress Candidate", "email": "candidate@example.com"}],
            cc_recipients=[],
            bcc_recipients=[],
            attachment_asset_ids=[],
            status=MailTaskStatus.SENT.value,
            sent_at=sent_at,
            data={
                MAIL_TASK_DATA_RENDER_CONTEXT_KEY: {
                    "job_progress": {
                        "id": progress.id,
                        "purpose": "assessment_invite",
                    }
                }
            },
        )
        setup_session.add(task)
        await setup_session.commit()
        await setup_session.refresh(task)
        task_id = int(task.id)

    updated = await sync_assessment_sent_at_from_mail_task(task_id)

    assert updated is True
    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.data["assessment_sent_at"] == "2026-06-24T08:45:00+00:00"


async def test_successful_assessment_mail_task_syncs_sent_at_by_marked_mail_task_id(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    _, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=True,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )
    sent_at = datetime(2026, 6, 24, 9, 15, tzinfo=UTC)

    async with local_session() as setup_session:
        account = MailAccount(
            admin_user_id=int(superadmin_credentials["id"]),
            email="assessment-sync-by-task@example.com",
            provider="qq",
            smtp_username="assessment-sync-by-task@example.com",
            smtp_host="smtp.qq.com",
            smtp_port=587,
            security_mode="starttls",
            auth_secret="smtp-auth-code",
            status="enabled",
            data={},
        )
        setup_session.add(account)
        await setup_session.flush()
        task = MailTask(
            account_id=account.id,
            template_id=None,
            signature_id=None,
            subject="Assessment",
            body_html="<p>Assessment</p>",
            to_recipients=[{"name": "Progress Candidate", "email": "candidate@example.com"}],
            cc_recipients=[],
            bcc_recipients=[],
            attachment_asset_ids=[],
            status=MailTaskStatus.SENT.value,
            sent_at=sent_at,
            data={},
        )
        setup_session.add(task)
        await setup_session.flush()

        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        saved.data = {
            **(saved.data or {}),
            "assessment_invited_at": "2026-06-24T09:00:00+00:00",
            "assessment_invite_mail_task_id": int(task.id),
        }
        await setup_session.commit()
        task_id = int(task.id)

    updated = await sync_assessment_sent_at_from_mail_task(task_id)

    assert updated is True
    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.data["assessment_sent_at"] == "2026-06-24T09:15:00+00:00"
