import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.core.security import get_password_hash
from src.app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_task.const import MAIL_TASK_DATA_RENDER_CONTEXT_KEY, MailTaskStatus
from src.app.modules.admin.mail_task.model import MailTask
from src.app.modules.assets.model import Asset
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.const import JOB_DATA_LANGUAGES_KEY
from src.app.modules.job.model import Job
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.job_progress.service import list_candidate_job_applications, sync_assessment_sent_at_from_mail_task
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
    if not any(field.get("key") == "native_languages" for field in fields):
        fields.insert(
            5,
            {
                "key": "native_languages",
                "label": "Native languages",
                "type": "text",
                "required": False,
                "canFilter": True,
            },
        )
    return fields


async def _create_progress_test_admin(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    *,
    suffix: str,
) -> tuple[int, dict[str, str]]:
    password = "AdminPass123!"
    username = f"adm{suffix.lower()}"[:20]
    admin = AdminUser(
        name="Progress Admin",
        username=username,
        email=f"admin.{suffix}@example.com",
        hashed_password=get_password_hash(password),
        phone=None,
        note="job progress language test admin",
        status="enabled",
        profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
        is_superuser=True,
        role_id=None,
        data={},
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    response = await admin_client.post(
        "/api/v1/auth/login",
        json={
            "username_or_email": username,
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    return int(admin.id), {"Authorization": f"Bearer {response.json()['access_token']}"}


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


async def test_job_progress_auto_assigns_language_when_country_and_native_language_match(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:8]
    owner_admin_user_id, admin_auth_headers = await _create_progress_test_admin(
        admin_client,
        db_session,
        suffix=suffix,
    )
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Language Match Role {suffix}",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    job.country = "Indonesia"
    job.data = {**(job.data or {}), JOB_DATA_LANGUAGES_KEY: ["Indonesian"]}
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=suffix, name="Language Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    application_items = build_application_items(
        full_name="Language Candidate",
        email=user.email,
        whatsapp="+62-3000-0000",
        nationality="Indonesian",
        country_of_residence="Indonesia",
        education_status="Bachelor's degree",
        resume_asset_id=resume.id,
    )
    application_items.append({"field_key": "native_languages", "value": "Indonesian"})

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={"items": application_items},
    )
    assert apply_response.status_code == 200, apply_response.text

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == apply_response.json()["application_id"])
        )
        progress = progress_result.scalar_one()
        assert progress.data["job_languages"] == "id-ID"

    list_response = await admin_client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    item = next(
        row for row in list_response.json()["items"] if row["application_id"] == apply_response.json()["application_id"]
    )
    assert item["process_data"]["job_languages"] == "id-ID"


async def test_job_progress_auto_assigns_none_when_country_does_not_match(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:8]
    owner_admin_user_id, admin_auth_headers = await _create_progress_test_admin(
        admin_client,
        db_session,
        suffix=suffix,
    )
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Language Country Mismatch Role {suffix}",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    job.country = "Indonesia"
    job.data = {**(job.data or {}), JOB_DATA_LANGUAGES_KEY: ["Indonesian"]}
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=suffix, name="Country Mismatch Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    application_items = build_application_items(
        full_name="Country Mismatch Candidate",
        email=user.email,
        whatsapp="+60-3000-0001",
        nationality="Malaysian",
        country_of_residence="Malaysia",
        education_status="Bachelor's degree",
        resume_asset_id=resume.id,
    )
    application_items.append({"field_key": "native_languages", "value": "Indonesian"})

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={"items": application_items},
    )
    assert apply_response.status_code == 200, apply_response.text

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == apply_response.json()["application_id"])
        )
        progress = progress_result.scalar_one()
        assert progress.data["job_languages"] == "无"

    list_response = await admin_client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    item = next(
        row for row in list_response.json()["items"] if row["application_id"] == apply_response.json()["application_id"]
    )
    assert item["process_data"]["job_languages"] == "无"


async def test_job_progress_auto_assigns_none_when_native_language_does_not_match(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:8]
    owner_admin_user_id, admin_auth_headers = await _create_progress_test_admin(
        admin_client,
        db_session,
        suffix=suffix,
    )
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Language Native Mismatch Role {suffix}",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    job.country = "Indonesia"
    job.data = {**(job.data or {}), JOB_DATA_LANGUAGES_KEY: ["Indonesian"]}
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=suffix, name="Native Mismatch Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    application_items = build_application_items(
        full_name="Native Mismatch Candidate",
        email=user.email,
        whatsapp="+62-3000-0002",
        nationality="Indonesian",
        country_of_residence="Indonesia",
        education_status="Bachelor's degree",
        resume_asset_id=resume.id,
    )
    application_items.append({"field_key": "native_languages", "value": "Malay"})

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={"items": application_items},
    )
    assert apply_response.status_code == 200, apply_response.text

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == apply_response.json()["application_id"])
        )
        progress = progress_result.scalar_one()
        assert progress.data["job_languages"] == "无"

    list_response = await admin_client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    item = next(
        row for row in list_response.json()["items"] if row["application_id"] == apply_response.json()["application_id"]
    )
    assert item["process_data"]["job_languages"] == "无"


async def test_job_progress_language_filter_normalizes_legacy_array_value(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:8]
    owner_admin_user_id, admin_auth_headers = await _create_progress_test_admin(
        admin_client,
        db_session,
        suffix=suffix,
    )
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Legacy Language Filter Role {suffix}",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=suffix, name="Legacy Language Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Legacy Language Candidate",
                email=user.email,
                whatsapp="+66-3000-0003",
                nationality="Thai",
                country_of_residence="Thailand",
                education_status="Bachelor's degree",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text

    async with local_session() as update_session:
        progress_result = await update_session.execute(
            select(JobProgress).where(JobProgress.application_id == apply_response.json()["application_id"])
        )
        progress = progress_result.scalar_one()
        next_data = dict(progress.data or {})
        next_data["job_languages"] = ["泰语", "英语"]
        progress.data = next_data
        await update_session.commit()

    list_response = await admin_client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
        params={
            "active_stage": "screening",
            "advanced_filter": json.dumps(
                {
                    "combinator": "and",
                    "rules": [
                        {
                            "field": "job_languages",
                            "operator": "contains",
                            "value": "泰语",
                        }
                    ],
                }
            ),
        },
    )
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["items"][0]["process_data"]["job_languages"] == "泰语"
    assert progress.id in list_response.json()["matched_progress_ids"]


async def test_update_job_progress_language_overrides_auto_value_for_selected_rows(
    admin_client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:8]
    owner_admin_user_id, admin_auth_headers = await _create_progress_test_admin(
        admin_client,
        db_session,
        suffix=suffix,
    )
    fields = _build_web_application_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Language Override Role {suffix}",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=fields,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
    )
    job.country = "Indonesia"
    job.data = {**(job.data or {}), JOB_DATA_LANGUAGES_KEY: ["Indonesian"]}
    await db_session.commit()

    user, password = await create_candidate_user(db_session, suffix=suffix, name="Language Override Candidate")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()

    application_items = build_application_items(
        full_name="Language Override Candidate",
        email=user.email,
        whatsapp="+62-3000-0004",
        nationality="Indonesian",
        country_of_residence="Indonesia",
        education_status="Bachelor's degree",
        resume_asset_id=resume.id,
    )
    application_items.append({"field_key": "native_languages", "value": "Indonesian"})

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={"items": application_items},
    )
    assert apply_response.status_code == 200, apply_response.text

    async with local_session() as assertion_session:
        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == apply_response.json()["application_id"])
        )
        progress = progress_result.scalar_one()
        progress_id = progress.id
        assert progress.data["job_languages"] == "id-ID"

    update_response = await admin_client.patch(
        f"/api/v1/jobs/{job.id}/progress/language",
        headers=admin_auth_headers,
        json={"progress_ids": [progress_id], "language": "fil-PH"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["updated_count"] == 1
    assert update_response.json()["updated_field_keys"] == ["job_languages"]

    list_response = await admin_client.get(f"/api/v1/jobs/{job.id}/progress", headers=admin_auth_headers)
    assert list_response.status_code == 200, list_response.text
    item = next(row for row in list_response.json()["items"] if row["id"] == progress_id)
    assert item["process_data"]["job_languages"] == "fil-PH"


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


async def test_rejected_progress_restores_to_recorded_assessment_stage(
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

    async with local_session() as setup_session:
        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        saved.current_stage = "assessment_review"
        saved.data = {
            **(saved.data or {}),
            "assessment_attachment": "candidate-assessment.xlsx",
            "assessment_result": "通过",
        }
        await setup_session.commit()

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "rejected"},
    )
    assert reject_response.status_code == 200, reject_response.text

    restore_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "assessment_review"},
    )
    assert restore_response.status_code == 200, restore_response.text

    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.current_stage == "assessment_review"
        assert saved.data["assessment_attachment"] == "candidate-assessment.xlsx"
        assert saved.data["assessment_result"] == "通过"
        assert "rejected_from_stage" not in saved.data
        candidate_page = await list_candidate_job_applications(
            user_id=progress.user_id,
            page=1,
            page_size=10,
            db=assertion_session,
        )
        candidate_item = next(item for item in candidate_page["items"] if item["job_progress_id"] == progress.id)
        assert candidate_item["current_stage"] == "assessment_review"
        assert candidate_item["candidate_visible_stage"] == "assessment_review"


async def test_rejected_progress_restore_rejects_mismatched_or_missing_source_stage(
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

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "rejected"},
    )
    assert reject_response.status_code == 200, reject_response.text

    mismatch_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "screening_passed"},
    )
    assert mismatch_response.status_code == 400

    async with local_session() as setup_session:
        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        saved.data = {key: value for key, value in (saved.data or {}).items() if key != "rejected_from_stage"}
        await setup_session.commit()

    missing_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "pending_screening"},
    )
    assert missing_response.status_code == 400


async def test_rejected_progress_restores_to_recorded_contract_pool_stage(
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
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )

    async with local_session() as setup_session:
        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        saved.current_stage = "contract_pool"
        saved.data = {**(saved.data or {}), "onboarding_status": "可发合同"}
        await setup_session.commit()

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "rejected"},
    )
    assert reject_response.status_code == 200, reject_response.text

    restore_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "contract_pool"},
    )
    assert restore_response.status_code == 200, restore_response.text

    async with local_session() as assertion_session:
        saved = await assertion_session.get(JobProgress, progress.id)
        assert saved is not None
        assert saved.current_stage == "contract_pool"
        assert saved.data["onboarding_status"] == "可发合同"
        assert "rejected_from_stage" not in saved.data


async def test_rejected_active_progress_restores_previous_contract_state(
    admin_client: AsyncClient,
    admin_auth_headers: dict[str, str],
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    application_payload, progress, _ = await _submit_application_for_progress(
        web_client,
        db_session,
        superadmin_credentials,
        assessment_enabled=False,
        automation_rules={"combinator": "and", "rules": []},
        education_status="Bachelor's degree (completed)",
    )

    async with local_session() as setup_session:
        saved = await setup_session.get(JobProgress, progress.id)
        assert saved is not None
        job = await setup_session.get(Job, saved.job_id)
        assert job is not None
        resume_result = await setup_session.execute(
            select(Asset).where(Asset.owner_id == progress.user_id).order_by(Asset.id.desc())
        )
        signed_asset = resume_result.scalars().first()
        assert signed_asset is not None
        saved.current_stage = "active"
        saved.data = {
            **(saved.data or {}),
            "onboarding_status": "已发大礼包",
            "gift_package_sent_at": "2026-07-10T08:00:00Z",
        }
        contract = ContractRecord(
            user_id=progress.user_id,
            user_snapshot_name="Progress Candidate",
            user_snapshot_email="progress@example.com",
            talent_profile_id=progress.talent_profile_id,
            application_id=int(application_payload["application_id"]),
            job_id=progress.job_id,
            job_progress_id=progress.id,
            job_snapshot_title="Progress Role",
            service_customer_company_id=int(job.company_id),
            service_customer_project_id=int(job.project_id),
            contract_status="Active",
            contractor_name="Progress Candidate",
            candidate_signed_contract_asset_id=signed_asset.id,
            end_date=date(2027, 1, 31),
            data={"contract_review": "审核通过"},
        )
        setup_session.add(contract)
        await setup_session.commit()
        contract_id = int(contract.id)

    reject_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "rejected"},
    )
    assert reject_response.status_code == 200, reject_response.text

    async with local_session() as rejected_session:
        rejected_progress = await rejected_session.get(JobProgress, progress.id)
        rejected_contract = await rejected_session.get(ContractRecord, contract_id)
        assert rejected_progress is not None
        assert rejected_contract is not None
        assert rejected_progress.data["rejected_from_stage"] == "active"
        assert rejected_progress.data["rejected_contract_previous_status"] == "Active"
        assert rejected_progress.data["rejected_contract_previous_end_date"] == "2027-01-31"
        assert rejected_contract.contract_status == "Terminated"

    restore_response = await admin_client.post(
        f"/api/v1/jobs/{progress.job_id}/progress/stage",
        headers=admin_auth_headers,
        json={"progress_ids": [progress.id], "target_stage": "active"},
    )
    assert restore_response.status_code == 200, restore_response.text

    async with local_session() as assertion_session:
        restored_progress = await assertion_session.get(JobProgress, progress.id)
        restored_contract = await assertion_session.get(ContractRecord, contract_id)
        assert restored_progress is not None
        assert restored_contract is not None
        assert restored_progress.current_stage == "active"
        assert restored_progress.data["onboarding_status"] == "已发大礼包"
        assert "rejected_from_stage" not in restored_progress.data
        assert "rejected_contract_previous_status" not in restored_progress.data
        assert "rejected_contract_previous_end_date" not in restored_progress.data
        assert restored_contract.contract_status == "Active"
        assert restored_contract.end_date == date(2027, 1, 31)


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
