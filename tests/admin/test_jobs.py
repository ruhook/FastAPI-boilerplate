import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_signature.model import MailSignature
from src.app.modules.admin.mail_task.const import MAIL_TASK_DATA_RENDER_CONTEXT_KEY
from src.app.modules.admin.mail_task.model import MailTask
from src.app.modules.admin.mail_template.model import MailTemplate
from src.app.modules.admin.mail_template_category.model import MailTemplateCategory
from tests.helpers.admin import create_admin_user, login_admin_user
from tests.helpers.talent import (
    build_application_items,
    build_automation_rules,
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
            "show_compensation": True,
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
            "compensation_min": None,
            "compensation_max": None,
            "show_compensation": False,
            "description": "<p>Updated description.</p>",
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated_payload = update_response.json()
    assert updated_payload["company"] == "T-Maxx Updated"
    assert updated_payload["compensation_min"] is None
    assert updated_payload["compensation_max"] is None
    assert updated_payload["show_compensation"] is False

    filtered_list_response = await client.get(
        "/api/v1/jobs",
        headers=admin_auth_headers,
        params={"company": "T-Maxx Updated"},
    )
    assert filtered_list_response.status_code == 200, filtered_list_response.text
    filtered_payload = filtered_list_response.json()
    assert any(item["id"] == job_id for item in filtered_payload["items"])


async def test_non_owner_can_view_job_but_cannot_update_it(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
) -> None:
    owner_admin, owner_password = await create_admin_user(
        db_session,
        role_id=None,
        name="Job Owner",
        username_prefix="owner",
    )
    other_admin, other_password = await create_admin_user(
        db_session,
        role_id=None,
        name="Job Viewer",
        username_prefix="viewer",
    )
    owner_headers = await login_admin_user(
        client,
        username_or_email=owner_admin.email,
        password=owner_password,
    )
    other_headers = await login_admin_user(
        client,
        username_or_email=other_admin.email,
        password=other_password,
    )
    template = await create_form_template(
        db_session,
        suffix="job-owner",
        fields=build_form_fields(),
    )
    job = await create_open_job(
        db_session,
        suffix="job-owner",
        title="Owner Editable Job",
        owner_admin_user_id=owner_admin.id,
        form_template_id=template.id,
        form_fields=build_form_fields(),
        assessment_enabled=False,
    )

    owner_detail_response = await client.get(f"/api/v1/jobs/{job.id}", headers=owner_headers)
    assert owner_detail_response.status_code == 200, owner_detail_response.text
    assert owner_detail_response.json()["can_edit"] is True

    other_detail_response = await client.get(f"/api/v1/jobs/{job.id}", headers=other_headers)
    assert other_detail_response.status_code == 200, other_detail_response.text
    assert other_detail_response.json()["can_edit"] is False

    list_response = await client.get("/api/v1/jobs", headers=other_headers)
    assert list_response.status_code == 200, list_response.text
    list_item = next(item for item in list_response.json()["items"] if item["id"] == job.id)
    assert list_item["can_edit"] is False

    superadmin_detail_response = await client.get(f"/api/v1/jobs/{job.id}", headers=admin_auth_headers)
    assert superadmin_detail_response.status_code == 200, superadmin_detail_response.text
    assert superadmin_detail_response.json()["can_edit"] is False

    forbidden_update_response = await client.patch(
        f"/api/v1/jobs/{job.id}",
        headers=other_headers,
        json={"status": "暂停"},
    )
    assert forbidden_update_response.status_code == 403, forbidden_update_response.text

    superadmin_update_response = await client.patch(
        f"/api/v1/jobs/{job.id}",
        headers=admin_auth_headers,
        json={"status": "暂停"},
    )
    assert superadmin_update_response.status_code == 403, superadmin_update_response.text

    owner_update_response = await client.patch(
        f"/api/v1/jobs/{job.id}",
        headers=owner_headers,
        json={"status": "暂停"},
    )
    assert owner_update_response.status_code == 200, owner_update_response.text
    assert owner_update_response.json()["status"] == "暂停"
    assert owner_update_response.json()["can_edit"] is True


async def test_admin_job_list_supports_sorting(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    template = await create_form_template(
        db_session,
        suffix="job-sort",
        fields=build_form_fields(),
    )
    owner_admin_user_id = int(superadmin_credentials["id"])
    alpha_job = await create_open_job(
        db_session,
        suffix="job-sort-alpha",
        title="Sort Probe Alpha",
        company_name="Alpha Sort Co",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=build_form_fields(),
        assessment_enabled=False,
    )
    beta_job = await create_open_job(
        db_session,
        suffix="job-sort-beta",
        title="Sort Probe Beta",
        company_name="Beta Sort Co",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=build_form_fields(),
        assessment_enabled=False,
    )
    charlie_job = await create_open_job(
        db_session,
        suffix="job-sort-charlie",
        title="Sort Probe Charlie",
        company_name="Charlie Sort Co",
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=template.id,
        form_fields=build_form_fields(),
        assessment_enabled=False,
    )
    alpha_job.applicant_count = 2
    beta_job.applicant_count = 12
    charlie_job.applicant_count = 7
    await db_session.commit()

    company_response = await client.get(
        "/api/v1/jobs",
        headers=admin_auth_headers,
        params={
            "keyword": "Sort Probe",
            "sort_by": "company",
            "sort_order": "ascend",
            "page_size": 10,
        },
    )
    assert company_response.status_code == 200, company_response.text
    assert [item["company"] for item in company_response.json()["items"]] == [
        "Alpha Sort Co",
        "Beta Sort Co",
        "Charlie Sort Co",
    ]

    applicants_response = await client.get(
        "/api/v1/jobs",
        headers=admin_auth_headers,
        params={
            "keyword": "Sort Probe",
            "sort_by": "applicants",
            "sort_order": "descend",
            "page_size": 10,
        },
    )
    assert applicants_response.status_code == 200, applicants_response.text
    assert [item["id"] for item in applicants_response.json()["items"]] == [
        beta_job.id,
        charlie_job.id,
        alpha_job.id,
    ]


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


async def test_auto_screening_match_creates_assessment_mail_task_and_stays_pending(
    client: AsyncClient,
    web_client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers: dict[str, str],
    superadmin_credentials: dict[str, str | int],
) -> None:
    admin_user_id = int(superadmin_credentials["id"])
    category = MailTemplateCategory(
        admin_user_id=admin_user_id,
        name="Assessment Notice",
        data={},
    )
    db_session.add(category)
    await db_session.flush()

    account = MailAccount(
        admin_user_id=admin_user_id,
        email="assessment-sender@example.com",
        provider="qq",
        smtp_username="assessment-sender@example.com",
        smtp_host="smtp.qq.com",
        smtp_port=587,
        security_mode="starttls",
        auth_secret="smtp-auth-code",
        status="enabled",
        data={},
    )
    template_record = MailTemplate(
        admin_user_id=admin_user_id,
        category_id=category.id,
        name="Assessment Template",
        subject_template="Assessment for {{job_title}}",
        body_html="<p>Hi {{candidate_name}}, please complete {{assessment_link}}.</p>",
        attachments=[],
        data={},
    )
    signature = MailSignature(
        admin_user_id=admin_user_id,
        name="Assessment Signature",
        owner="Recruiting",
        enabled=True,
        full_name="Recruiting Team",
        job_title="Recruiter",
        company_name="T-Maxx",
        primary_email="assessment-sender@example.com",
        data={},
    )
    db_session.add_all([account, template_record, signature])
    await db_session.commit()
    await db_session.refresh(account)
    await db_session.refresh(template_record)
    await db_session.refresh(signature)

    form_template = await create_form_template(
        db_session,
        suffix="assessment-mail",
        fields=build_form_fields(),
    )
    job = await create_open_job(
        db_session,
        suffix="assessment-mail",
        title="Assessment Mail Job",
        company_name="Assessment Ops",
        owner_admin_user_id=admin_user_id,
        form_template_id=form_template.id,
        form_fields=build_form_fields(),
        assessment_enabled=True,
        automation_rules=build_automation_rules(
            field_key="education_status",
            operator="equals",
            value="Bachelor’s degree (completed)",
        ),
    )
    job.assessment_mail_account_id = account.id
    job.assessment_mail_template_id = template_record.id
    job.assessment_mail_signature_id = signature.id
    await db_session.commit()
    await db_session.refresh(job)

    user, password = await create_candidate_user(
        db_session,
        suffix="assessmentmail",
        name="Assessment Mail Candidate",
    )
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix="assessment-mail")

    apply_response = await web_client.post(
        f"/api/v1/jobs/{job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Assessment Mail Candidate",
                email=user.email,
                whatsapp="+55-5100-0000",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=resume.id,
            )
        },
    )
    assert apply_response.status_code == 200, apply_response.text
    application_id = int(apply_response.json()["application_id"])

    progress_response = await client.get(
        f"/api/v1/jobs/{job.id}/progress",
        headers=admin_auth_headers,
    )
    assert progress_response.status_code == 200, progress_response.text
    progress_item = progress_response.json()["items"][0]
    assert progress_item["current_stage"] == "pending_screening"

    task_result = await db_session.execute(select(MailTask).where(MailTask.account_id == account.id))
    mail_task = task_result.scalar_one_or_none()
    assert mail_task is not None
    assert mail_task.template_id == template_record.id
    assert mail_task.signature_id == signature.id
    assert mail_task.status == "pending"
    assert mail_task.to_recipients == [{"name": "Assessment Mail Candidate", "email": user.email}]
    task_context = (mail_task.data or {}).get(MAIL_TASK_DATA_RENDER_CONTEXT_KEY) or {}
    assert task_context["job"]["assessment_link"].endswith(f"/my-jobs/{application_id}")
