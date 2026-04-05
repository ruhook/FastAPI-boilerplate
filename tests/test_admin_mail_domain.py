import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio(loop_scope="session")


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def test_superadmin_can_manage_mail_templates_signatures_assets_and_send_tasks(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    account_response = await client.post(
        "/api/v1/mail/accounts",
        headers=admin_auth_headers,
        json={
            "email": "templates@example.com",
            "provider": "qq",
            "auth_secret": "smtp-auth-code",
            "status": "enabled",
        },
    )
    assert account_response.status_code == 201, account_response.text
    account_id = account_response.json()["id"]

    attachment_response = await client.post(
        "/api/v1/assets/upload",
        headers=admin_auth_headers,
        data={
            "type": "file",
            "module": "mail",
        },
        files={"file": ("brief.pdf", b"%PDF-1.4 test file\n", "application/pdf")},
    )
    assert attachment_response.status_code == 201, attachment_response.text
    attachment_asset = attachment_response.json()
    assert attachment_asset["type"] == "file"
    assert attachment_asset["original_name"] == "brief.pdf"
    assert attachment_asset["url"] == attachment_asset["preview_url"]

    attachment_detail_response = await client.get(
        f"/api/v1/assets/{attachment_asset['id']}",
        headers=admin_auth_headers,
    )
    assert attachment_detail_response.status_code == 200, attachment_detail_response.text
    assert attachment_detail_response.json()["url"] == attachment_asset["preview_url"]

    preview_response = await client.get(attachment_asset["preview_url"], headers=admin_auth_headers)
    assert preview_response.status_code == 200, preview_response.text
    assert preview_response.headers["content-type"].startswith("application/pdf")

    download_response = await client.get(attachment_asset["download_url"], headers=admin_auth_headers)
    assert download_response.status_code == 200, download_response.text
    assert "attachment;" in download_response.headers["content-disposition"]

    avatar_response = await client.post(
        "/api/v1/assets/upload",
        headers=admin_auth_headers,
        data={
            "type": "image",
            "module": "mail",
        },
        files={"file": ("avatar.png", PNG_BYTES, "image/png")},
    )
    assert avatar_response.status_code == 201, avatar_response.text
    avatar_asset = avatar_response.json()

    banner_response = await client.post(
        "/api/v1/assets/upload",
        headers=admin_auth_headers,
        data={
            "type": "image",
            "module": "mail",
        },
        files={"file": ("banner.png", PNG_BYTES, "image/png")},
    )
    assert banner_response.status_code == 201, banner_response.text
    banner_asset = banner_response.json()

    root_category_response = await client.post(
        "/api/v1/mail/template-categories",
        headers=admin_auth_headers,
        json={"name": "测试题", "sort_order": 1, "enabled": True},
    )
    assert root_category_response.status_code == 201, root_category_response.text
    root_category_id = root_category_response.json()["id"]

    child_category_response = await client.post(
        "/api/v1/mail/template-categories",
        headers=admin_auth_headers,
        json={"name": "UK", "parent_id": root_category_id, "sort_order": 1, "enabled": True},
    )
    assert child_category_response.status_code == 201, child_category_response.text
    child_category = child_category_response.json()
    child_category_id = child_category["id"]
    assert child_category["parent_id"] == root_category_id

    category_list_response = await client.get(
        "/api/v1/mail/template-categories",
        headers=admin_auth_headers,
    )
    assert category_list_response.status_code == 200, category_list_response.text
    assert len(category_list_response.json()) == 2

    template_response = await client.post(
        "/api/v1/mail/templates",
        headers=admin_auth_headers,
        json={
            "name": "UK 测试题通知",
            "category_id": child_category_id,
            "subject_template": "请完成 {{job_title}} 测试",
            "body_html": "<p>Hi {{candidate_name}}，请在 {{due_date}} 前完成测试。</p>",
            "attachments": [{"asset_id": attachment_asset["id"]}],
        },
    )
    assert template_response.status_code == 201, template_response.text
    template = template_response.json()
    template_id = template["id"]
    assert template["variables"] == ["candidate_name", "due_date", "job_title"]
    assert template["attachments"][0]["asset_id"] == attachment_asset["id"]

    template_update_response = await client.patch(
        f"/api/v1/mail/templates/{template_id}",
        headers=admin_auth_headers,
        json={
            "subject_template": "请提交 {{job_title}} 测试结果",
        },
    )
    assert template_update_response.status_code == 200, template_update_response.text
    assert template_update_response.json()["subject_template"] == "请提交 {{job_title}} 测试结果"

    signature_response = await client.post(
        "/api/v1/mail/signatures",
        headers=admin_auth_headers,
        json={
            "name": "招聘标准签名",
            "owner": "Recruiting",
            "enabled": True,
            "full_name": "Betty Xie",
            "job_title": "Global Consultant",
            "company_name": "T-Maxx",
            "primary_email": "bettyxie@t-maxx.cc",
            "secondary_email": "betty-recruit@t-maxx.cc",
            "website": "https://www.t-maxx.cc",
            "linkedin_label": "Find our T-Maxx job posts on LinkedIn",
            "linkedin_url": "https://www.linkedin.com/company/t-maxx",
            "address": "Room 117, No.6, Langyuan, Chaoyang, Beijing, China",
            "avatar_asset_id": avatar_asset["id"],
            "banner_asset_id": banner_asset["id"],
        },
    )
    assert signature_response.status_code == 201, signature_response.text
    signature = signature_response.json()
    signature_id = signature["id"]
    assert signature["avatar_asset"]["id"] == avatar_asset["id"]
    assert signature["banner_asset"]["id"] == banner_asset["id"]
    assert "/api/v1/assets/" in signature["html"]

    signature_update_response = await client.patch(
        f"/api/v1/mail/signatures/{signature_id}",
        headers=admin_auth_headers,
        json={"job_title": "Senior Global Consultant"},
    )
    assert signature_update_response.status_code == 200, signature_update_response.text
    assert signature_update_response.json()["job_title"] == "Senior Global Consultant"

    task_response = await client.post(
        "/api/v1/mail/send",
        headers=admin_auth_headers,
        json={
            "account_id": account_id,
            "template_id": template_id,
            "signature_id": signature_id,
            "subject": "请完成测试",
            "body_html": "<p>Hi {{candidate_name}}</p>",
            "to_recipients": [{"name": "Candidate A", "email": "candidate@example.com"}],
            "attachment_asset_ids": [attachment_asset["id"]],
        },
    )
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    assert task["account_id"] == account_id
    assert task["template_id"] == template_id
    assert task["signature_id"] == signature_id
    assert task["status"] == "pending"
    assert task["attachment_asset_ids"] == [attachment_asset["id"]]

    delete_template_response = await client.delete(
        f"/api/v1/mail/templates/{template_id}",
        headers=admin_auth_headers,
    )
    assert delete_template_response.status_code == 200, delete_template_response.text

    delete_signature_response = await client.delete(
        f"/api/v1/mail/signatures/{signature_id}",
        headers=admin_auth_headers,
    )
    assert delete_signature_response.status_code == 200, delete_signature_response.text

    delete_child_category_response = await client.delete(
        f"/api/v1/mail/template-categories/{child_category_id}",
        headers=admin_auth_headers,
    )
    assert delete_child_category_response.status_code == 200, delete_child_category_response.text

    delete_root_category_response = await client.delete(
        f"/api/v1/mail/template-categories/{root_category_id}",
        headers=admin_auth_headers,
    )
    assert delete_root_category_response.status_code == 200, delete_root_category_response.text

    delete_account_response = await client.delete(
        f"/api/v1/mail/accounts/{account_id}",
        headers=admin_auth_headers,
    )
    assert delete_account_response.status_code == 400, delete_account_response.text
    assert "发信任务记录" in delete_account_response.json()["detail"]
