import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from ..app.core.db.database import local_session
from ..app.main_admin import app as admin_app
from ..app.main_web import app as web_app
from ..app.modules.admin.mail_task.model import MailTask
from ..app.modules.operation_log.const import OperationLogType
from .create_assessment_reviewer import (
    DEFAULT_EMAIL as DEFAULT_REVIEWER_EMAIL,
    DEFAULT_NAME as DEFAULT_REVIEWER_NAME,
    DEFAULT_PASSWORD as DEFAULT_REVIEWER_PASSWORD,
    DEFAULT_USERNAME as DEFAULT_REVIEWER_USERNAME,
    ensure_reviewer_account,
    ensure_reviewer_role,
)
from .run_client_apply_demo import (
    ensure_resume_asset,
    fetch_current_user,
    login_candidate,
    register_or_reuse_candidate,
    submit_application,
)
from .run_client_assessment_upload_demo import build_demo_pdf_bytes, upload_assessment
from .seed_apply_demo_flow import DEMO_ADMIN_PASSWORD, DEMO_ADMIN_USERNAME
from .seed_job_progress_demo_flow import (
    DEFAULT_CANDIDATE_EMAIL,
    DEFAULT_CANDIDATE_NAME,
    DEFAULT_CANDIDATE_PASSWORD,
    DEMO_JOB_DEFINITIONS,
    build_application_items,
    ensure_assessment_mail_dependencies,
    ensure_rejection_mail_dependencies,
    fetch_progress_payload,
    seed_admin_and_jobs,
)

WEB_BASE_URL = "http://testserver/api/v1"
ADMIN_BASE_URL = "http://testserver/api/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an end-to-end recruitment flow demo with clear step prints.")
    parser.add_argument("--candidate-name", default=DEFAULT_CANDIDATE_NAME, help="Candidate display name.")
    parser.add_argument("--candidate-email", default=DEFAULT_CANDIDATE_EMAIL, help="Candidate email.")
    parser.add_argument("--candidate-password", default=DEFAULT_CANDIDATE_PASSWORD, help="Candidate password.")
    parser.add_argument("--reviewer-name", default=DEFAULT_REVIEWER_NAME, help="Assessment reviewer display name.")
    parser.add_argument("--reviewer-email", default=DEFAULT_REVIEWER_EMAIL, help="Assessment reviewer email.")
    parser.add_argument("--reviewer-username", default=DEFAULT_REVIEWER_USERNAME, help="Assessment reviewer username.")
    parser.add_argument("--reviewer-password", default=DEFAULT_REVIEWER_PASSWORD, help="Assessment reviewer password.")
    return parser.parse_args()


def print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def print_detail(message: str) -> None:
    print(f"  - {message}")


def ensure_ok(response: httpx.Response, message: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{message}: {response.status_code} {response.text}")
    return response.json()


async def login_admin(
    client: httpx.AsyncClient,
    *,
    username_or_email: str,
    password: str,
) -> dict[str, Any]:
    response = await client.post(
        "/auth/login",
        json={
            "username_or_email": username_or_email,
            "password": password,
        },
    )
    payload = ensure_ok(response, "Admin login failed")
    print_detail(f"admin login succeeded: {payload['user']['username']}")
    return payload


async def list_mail_tasks_for_recipient(email: str) -> list[MailTask]:
    async with local_session() as session:
        result = await session.execute(
            select(MailTask).order_by(MailTask.id.asc())
        )
        items = []
        for task in result.scalars().all():
            recipients = task.to_recipients or []
            if any(str(item.get("email") or "").strip().lower() == email.strip().lower() for item in recipients):
                items.append(task)
        return items


async def admin_get_job_progress(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/jobs/{job_id}/progress",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    payload = ensure_ok(response, f"List job progress failed for job {job_id}")
    return payload.get("items", [])


async def admin_list_jobs(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> list[dict[str, Any]]:
    response = await client.get(
        "/jobs",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    payload = ensure_ok(response, "List jobs failed")
    return payload.get("items", [])


async def admin_update_assessment_review(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_ids: list[int],
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.patch(
        f"/jobs/{job_id}/progress/assessment-review",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "progress_ids": progress_ids,
            **payload,
        },
    )
    return ensure_ok(response, "Update assessment review failed")


async def admin_execute_assessment_automation(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_ids: list[int],
) -> dict[str, Any]:
    response = await client.post(
        f"/jobs/{job_id}/progress/assessment-automation",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"progress_ids": progress_ids},
    )
    return ensure_ok(response, "Execute assessment automation failed")


async def admin_move_stage(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_ids: list[int],
    target_stage: str,
    reason: str = "",
) -> dict[str, Any]:
    response = await client.post(
        f"/jobs/{job_id}/progress/stage",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "progress_ids": progress_ids,
            "target_stage": target_stage,
            "reason": reason,
        },
    )
    return ensure_ok(response, f"Move stage to {target_stage} failed")


async def admin_upload_contract_file(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    progress_id: int,
    file_name: str,
    note: str,
    endpoint: str,
) -> dict[str, Any]:
    file_bytes = build_demo_pdf_bytes(candidate_email=file_name, note=note)
    response = await client.post(
        endpoint.format(job_id=job_id),
        headers={"Authorization": f"Bearer {access_token}"},
        files={
            "progress_id": (None, str(progress_id)),
            "file": (file_name, file_bytes, "application/pdf"),
        },
    )
    return ensure_ok(response, f"Upload file failed at {endpoint}")


async def admin_send_mail(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    account_id: int,
    template_id: int,
    signature_id: int,
    candidate_name: str,
    candidate_email: str,
    attachment_asset_ids: list[int],
    render_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/mail/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "account_id": account_id,
            "template_id": template_id,
            "signature_id": signature_id,
            "subject": "联调流程合同发送",
            "body_html": "<p>联调流程邮件发送测试。</p>",
            "to_recipients": [
                {
                    "name": candidate_name,
                    "email": candidate_email,
                }
            ],
            "attachment_asset_ids": attachment_asset_ids,
            "render_context": render_context or {},
        },
    )
    return ensure_ok(response, "Create mail task failed")


async def admin_get_talent(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    talent_id: int,
) -> dict[str, Any]:
    response = await client.get(
        f"/talents/{talent_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return ensure_ok(response, f"Get talent {talent_id} failed")


async def candidate_upload_signed_contract(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    job_id: int,
    candidate_email: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/jobs/{job_id}/signed-contract/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files={
            "file": (
                "candidate-signed-contract.pdf",
                build_demo_pdf_bytes(
                    candidate_email=candidate_email,
                    note="Recruitment E2E candidate signed contract.",
                ),
                "application/pdf",
            )
        },
    )
    return ensure_ok(response, "Candidate signed contract upload failed")


def normalize_candidate_email(candidate_email: str) -> str:
    if candidate_email != DEFAULT_CANDIDATE_EMAIL:
        return candidate_email.strip()
    timestamp = datetime.now().strftime("%m%d%H%M%S")
    local_part, _, domain = candidate_email.partition("@")
    trimmed_local = local_part[:12] or "cand"
    return f"{trimmed_local}.{timestamp}@{domain or 'example.com'}"


def require_stage(progress_payload: dict[str, Any], expected_stage: str, label: str) -> None:
    current_stage = progress_payload.get("current_stage")
    if current_stage != expected_stage:
        raise RuntimeError(f"{label} expected stage={expected_stage}, got {current_stage}")
    print_detail(f"{label}: {progress_payload.get('current_stage_cn_name')} ({current_stage})")


async def main() -> None:
    args = parse_args()
    candidate_email = normalize_candidate_email(args.candidate_email)

    print_step("环节 1/10：准备管理员、判题人和演示岗位")
    seed_payload, jobs = await seed_admin_and_jobs()

    reviewer_role = await ensure_reviewer_role(role_name="测试题判题人")
    reviewer_account = await ensure_reviewer_account(
        role_id=reviewer_role.id,
        name=args.reviewer_name,
        email=args.reviewer_email,
        username=args.reviewer_username,
        password=args.reviewer_password,
        reset_password=True,
    )
    async with httpx.AsyncClient(
        transport=ASGITransport(app=admin_app),
        base_url=ADMIN_BASE_URL,
        timeout=30.0,
    ) as admin_client, httpx.AsyncClient(
        transport=ASGITransport(app=web_app),
        base_url=WEB_BASE_URL,
        timeout=30.0,
    ) as web_client:
        admin_login_payload = await login_admin(
            admin_client,
            username_or_email=seed_payload["admin"]["username"],
            password=seed_payload["admin"]["password"],
        )
        admin_access_token = admin_login_payload["access_token"]
        admin_user_id = int(admin_login_payload["user"]["id"])
        mail_ids = await ensure_assessment_mail_dependencies(admin_user_id=admin_user_id)
        rejection_mail_ids = await ensure_rejection_mail_dependencies(admin_user_id=admin_user_id)
        print_detail(f"admin={seed_payload['admin']['username']} reviewer={reviewer_account['username']}")
        print_detail(f"jobs={[(job.id, job.title) for job in jobs]}")

        print_step("环节 2/10：C 端注册并投递 4 个岗位")
        await register_or_reuse_candidate(
            web_client,
            name=args.candidate_name,
            email=candidate_email,
            password=args.candidate_password,
        )
        candidate_access_token = await login_candidate(
            web_client,
            email=candidate_email,
            password=args.candidate_password,
        )
        current_user = await fetch_current_user(web_client, access_token=candidate_access_token)
        resume_asset = await ensure_resume_asset(user_id=int(current_user["id"]), email=candidate_email)
        print_detail(f"candidate={candidate_email} user_id={current_user['id']} resume_asset_id={resume_asset.id}")

        applications: list[dict[str, Any]] = []
        for definition, job in zip(DEMO_JOB_DEFINITIONS, jobs, strict=True):
            items = build_application_items(
                scenario_key=definition["key"],
                candidate_name=args.candidate_name,
                candidate_email=candidate_email,
                resume_asset_id=resume_asset.id,
            )
            apply_payload = await submit_application(
                web_client,
                access_token=candidate_access_token,
                job_id=job.id,
                items=items,
            )
            progress_payload = await fetch_progress_payload(application_id=int(apply_payload["application_id"]))
            require_stage(progress_payload, definition["expected_stage"], job.title)
            applications.append(
                {
                    "job": job,
                    "definition": definition,
                    "application": apply_payload,
                    "progress": progress_payload,
                }
            )

        assessment_case = next(item for item in applications if item["definition"]["key"] == "assessment_auto_pass")
        no_assessment_case = next(item for item in applications if item["definition"]["key"] == "no_assessment_auto_pass")
        manual_case = next(item for item in applications if item["definition"]["key"] == "assessment_manual_pending")
        rejected_case = next(item for item in applications if item["definition"]["key"] == "no_assessment_auto_rejected")

        auto_mail_tasks = await list_mail_tasks_for_recipient(candidate_email)
        auto_subjects = {str(task.subject) for task in auto_mail_tasks}
        if "请完成 {{job_title}} 测试题" not in auto_subjects:
            raise RuntimeError("Missing auto-created assessment mail task for the assessment-review branch.")
        if "关于 {{job_title}} 的申请结果通知" not in auto_subjects:
            raise RuntimeError("Missing auto-created rejection mail task for the auto-rejected branch.")
        print_detail(
            "auto mail tasks created: "
            f"assessment_account={mail_ids['mail_account_id']} rejection_account={rejection_mail_ids['mail_account_id']}"
        )

        print_step("环节 3/10：校验判题权限账号未分配前不可见")
        reviewer_login_payload = await login_admin(
            admin_client,
            username_or_email=str(reviewer_account["username"]),
            password=args.reviewer_password,
        )
        reviewer_access_token = reviewer_login_payload["access_token"]
        reviewer_jobs_before = await admin_list_jobs(admin_client, access_token=reviewer_access_token)
        print_detail(f"reviewer visible jobs before assignment = {len(reviewer_jobs_before)}")

        print_step("环节 4/10：B 端给测试题回收记录分配判题负责人")
        assessment_progress_id = int(assessment_case["progress"]["id"])
        await admin_update_assessment_review(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
            payload={
                "assessment_reviewer": args.reviewer_name,
                "assessment_reviewer_admin_user_id": int(reviewer_account["id"]),
            },
        )
        print_detail(
            f"assigned reviewer={args.reviewer_name} progress_id={assessment_progress_id} job_id={assessment_case['job'].id}"
        )

        reviewer_jobs_after = await admin_list_jobs(admin_client, access_token=reviewer_access_token)
        reviewer_progress_after = await admin_get_job_progress(
            admin_client,
            access_token=reviewer_access_token,
            job_id=int(assessment_case["job"].id),
        )
        print_detail(f"reviewer visible jobs after assignment = {len(reviewer_jobs_after)}")
        print_detail(f"reviewer visible assessment rows after assignment = {len(reviewer_progress_after)}")

        print_step("环节 5/10：C 端上传测试题附件")
        upload_payload = await upload_assessment(
            web_client,
            access_token=candidate_access_token,
            job_id=int(assessment_case["job"].id),
            file_name="assessment-answer.pdf",
            file_bytes=build_demo_pdf_bytes(
                candidate_email=candidate_email,
                note="Recruitment E2E demo assessment attachment.",
            ),
        )
        print_detail(
            f"assessment asset uploaded: asset_id={upload_payload['assessment_asset']['id']} "
            f"name={upload_payload['assessment_asset']['original_name']}"
        )

        print_step("环节 6/10：判题人更新测试结果并执行自动化")
        await admin_update_assessment_review(
            admin_client,
            access_token=reviewer_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
            payload={
                "assessment_result": "通过",
                "assessment_review_comment": "联调脚本判定通过，进入筛选通过。",
            },
        )
        automation_payload = await admin_execute_assessment_automation(
            admin_client,
            access_token=reviewer_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
        )
        print_detail(json.dumps(automation_payload, ensure_ascii=False))
        progressed_after_automation = await fetch_progress_payload(
            application_id=int(assessment_case["application"]["application_id"])
        )
        require_stage(progressed_after_automation, "screening_passed", "测试题自动化后阶段")

        print_step("环节 7/10：B 端推进到合同库并上传合同")
        await admin_move_stage(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
            target_stage="contract_pool",
            reason="e2e_to_contract_pool",
        )
        after_contract_pool = await fetch_progress_payload(
            application_id=int(assessment_case["application"]["application_id"])
        )
        require_stage(after_contract_pool, "contract_pool", "进入合同库")

        draft_upload_payload = await admin_upload_contract_file(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_id=assessment_progress_id,
            file_name="contract-draft.pdf",
            note="Recruitment E2E draft contract.",
            endpoint="/jobs/{job_id}/progress/contract-draft/upload",
        )
        draft_asset_id = int(draft_upload_payload["contract_draft_asset"]["id"])
        print_detail(f"uploaded contract draft asset_id={draft_asset_id}")

        print_step("环节 8/10：B 端创建发邮件任务并附带待签合同")
        mail_task = await admin_send_mail(
            admin_client,
            access_token=admin_access_token,
            account_id=mail_ids["mail_account_id"],
            template_id=mail_ids["mail_template_id"],
            signature_id=mail_ids["mail_signature_id"],
            candidate_name=args.candidate_name,
            candidate_email=candidate_email,
            attachment_asset_ids=[draft_asset_id],
            render_context={
                "candidate_name": args.candidate_name,
                "candidate_email": candidate_email,
                "job_title": assessment_case["job"].title,
            },
        )
        print_detail(
            f"mail task created id={mail_task['id']} status={mail_task['status']} "
            f"attachments={mail_task['attachment_asset_ids']}"
        )

        print_step("环节 9/10：C 端上传人选签回合同，B 端上传公司盖章合同并流转到在职/汰换")
        signed_contract_payload = await candidate_upload_signed_contract(
            web_client,
            access_token=candidate_access_token,
            job_id=int(assessment_case["job"].id),
            candidate_email=candidate_email,
        )
        print_detail(
            f"candidate signed contract uploaded asset_id="
            f"{signed_contract_payload['candidate_signed_contract_asset']['id']}"
        )
        sealed_upload_payload = await admin_upload_contract_file(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_id=assessment_progress_id,
            file_name="company-sealed-contract.pdf",
            note="Recruitment E2E company sealed contract.",
            endpoint="/jobs/{job_id}/progress/company-sealed-contract/upload",
        )
        print_detail(
            f"uploaded company sealed contract asset_id={sealed_upload_payload['company_sealed_contract_asset']['id']}"
        )
        await admin_move_stage(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
            target_stage="active",
            reason="e2e_to_active",
        )
        after_active = await fetch_progress_payload(
            application_id=int(assessment_case["application"]["application_id"])
        )
        require_stage(after_active, "active", "进入在职")
        await admin_move_stage(
            admin_client,
            access_token=admin_access_token,
            job_id=int(assessment_case["job"].id),
            progress_ids=[assessment_progress_id],
            target_stage="replaced",
            reason="e2e_to_replaced",
        )
        after_replaced = await fetch_progress_payload(
            application_id=int(assessment_case["application"]["application_id"])
        )
        require_stage(after_replaced, "replaced", "进入汰换")

        print_step("环节 10/10：回查人才日志与其它分支状态")
        talent_detail = await admin_get_talent(
            admin_client,
            access_token=admin_access_token,
            talent_id=int(assessment_case["application"]["talent_profile_id"]),
        )
        logs = talent_detail.get("logs", [])
        relevant_logs = [
            log
            for log in logs
            if log.get("log_type")
            in {
                OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value,
                OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value,
                OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value,
                OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
                OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
                OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
            }
        ][:8]
        for log in relevant_logs:
            print_detail(
                f"log title={log.get('title')} source={log.get('actor_name') or log.get('actor_type')} "
                f"status={log.get('status_label') or '-'}"
            )

        print_detail(
            f"branch check: no-assessment auto pass -> {no_assessment_case['progress']['current_stage_cn_name']}"
        )
        print_detail(
            f"branch check: assessment manual pending -> {manual_case['progress']['current_stage_cn_name']}"
        )
        print_detail(
            f"branch check: automation rejected -> {rejected_case['progress']['current_stage_cn_name']}"
        )

        print_step("完成")
        print(
            json.dumps(
                {
                    "admin": seed_payload["admin"],
                    "reviewer": {
                        "username": reviewer_account["username"],
                        "email": reviewer_account["email"],
                        "password": args.reviewer_password,
                    },
                    "candidate": {
                        "name": args.candidate_name,
                        "email": candidate_email,
                        "password": args.candidate_password,
                        "user_id": current_user["id"],
                    },
                    "jobs": [
                        {
                            "id": int(item["job"].id),
                            "title": item["job"].title,
                            "application_id": int(item["application"]["application_id"]),
                            "talent_profile_id": int(item["application"]["talent_profile_id"]),
                            "final_stage": (
                                after_replaced["current_stage"]
                                if item is assessment_case
                                else item["progress"]["current_stage"]
                            ),
                        }
                        for item in applications
                    ],
                    "mail_task_id": mail_task["id"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
