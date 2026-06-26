from __future__ import annotations

import json

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.assets import feishu_preview
from src.app.modules.assets.model import Asset


@pytest.mark.asyncio(loop_scope="session")
async def test_superadmin_can_request_feishu_preview_for_spreadsheet_asset(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded_response = await client.post(
        "/api/v1/assets/upload",
        headers=admin_auth_headers,
        data={"type": "file", "module": "mail"},
        files={
            "file": (
                "assessment.xlsx",
                b"fake xlsx bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert uploaded_response.status_code == 201, uploaded_response.text
    asset_id = uploaded_response.json()["id"]

    async def fake_build_preview(*, asset, content):
        assert asset.id == asset_id
        assert content == b"fake xlsx bytes"
        return feishu_preview.FeishuPreviewResult(
            provider="feishu",
            url="https://tmx-intl.feishu.cn/sheets/fake-preview-token",
            document_token="fake-preview-token",
            document_type="sheet",
            cached=False,
        )

    monkeypatch.setattr(feishu_preview, "build_feishu_spreadsheet_preview", fake_build_preview)

    preview_response = await client.get(
        f"/api/v1/assets/{asset_id}/feishu-preview",
        headers=admin_auth_headers,
    )

    assert preview_response.status_code == 200, preview_response.text
    assert preview_response.json() == {
        "provider": "feishu",
        "url": "https://tmx-intl.feishu.cn/sheets/fake-preview-token",
        "document_token": "fake-preview-token",
        "document_type": "sheet",
        "cached": False,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_feishu_preview_rejects_non_spreadsheet_asset(
    client: AsyncClient,
    admin_auth_headers: dict[str, str],
) -> None:
    uploaded_response = await client.post(
        "/api/v1/assets/upload",
        headers=admin_auth_headers,
        data={"type": "file", "module": "mail"},
        files={"file": ("brief.pdf", b"%PDF-1.4 test file\n", "application/pdf")},
    )
    assert uploaded_response.status_code == 201, uploaded_response.text
    asset_id = uploaded_response.json()["id"]

    preview_response = await client.get(
        f"/api/v1/assets/{asset_id}/feishu-preview",
        headers=admin_auth_headers,
    )

    assert preview_response.status_code == 400
    assert "spreadsheet" in preview_response.text.lower()


@pytest.mark.no_database_cleanup
def test_extract_feishu_folder_token_from_copied_folder_link() -> None:
    assert (
        feishu_preview.extract_feishu_folder_token(
            "https://example.feishu.cn/drive/folder/fldExamplePreviewToken?from=from_copylink"
        )
        == "fldExamplePreviewToken"
    )


@pytest.mark.no_database_cleanup
def test_feishu_preview_configuration_allows_app_managed_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = Asset(
        type="file",
        module="mail",
        owner_type="admin_user",
        owner_id=1,
        original_name="assessment.xlsx",
        storage_key="hr-assets/mail/file/assessment.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=128,
        data={"suffix": "xlsx"},
    )
    monkeypatch.setattr(feishu_preview.settings, "FEISHU_PREVIEW_ENABLED", True)
    monkeypatch.setattr(feishu_preview.settings, "FEISHU_APP_ID", "cli_preview")
    monkeypatch.setattr(feishu_preview.settings, "FEISHU_APP_SECRET", SecretStr("secret"))
    monkeypatch.setattr(feishu_preview.settings, "FEISHU_PREVIEW_FOLDER_URL", "")

    assert feishu_preview._ensure_feishu_preview_configured(asset) == ""


@pytest.mark.no_database_cleanup
def test_spreadsheet_signature_changes_when_asset_changes() -> None:
    signature = feishu_preview.build_asset_preview_signature(
        asset_id=42,
        storage_key="hr-assets/progress/file/assessment.xlsx",
        file_size=128,
        original_name="assessment.xlsx",
        updated_at_iso="2026-06-25T10:30:00+08:00",
    )

    assert signature != feishu_preview.build_asset_preview_signature(
        asset_id=42,
        storage_key="hr-assets/progress/file/assessment.xlsx",
        file_size=129,
        original_name="assessment.xlsx",
        updated_at_iso="2026-06-25T10:30:00+08:00",
    )


@pytest.mark.no_database_cleanup
def test_feishu_response_error_includes_operation_code_and_message() -> None:
    response = httpx.Response(
        403,
        json={"code": 99991663, "msg": "forbidden"},
    )

    with pytest.raises(BadRequestException) as exc_info:
        feishu_preview._parse_feishu_response(response, operation="upload source file")

    detail = str(exc_info.value)
    assert "upload source file" in detail
    assert "99991663" in detail
    assert "forbidden" in detail


@pytest.mark.no_database_cleanup
@pytest.mark.asyncio(loop_scope="session")
async def test_upload_import_source_media_uses_app_managed_upload_point() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {"file_token": "media-token"}})

    asset = Asset(
        type="file",
        module="mail",
        owner_type="admin_user",
        owner_id=1,
        original_name="assessment.xlsx",
        storage_key="hr-assets/mail/file/assessment.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=128,
        data={"suffix": "xlsx"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        file_token = await feishu_preview._upload_import_source_media(
            client,
            token="tenant-token",
            asset=asset,
            content=b"xlsx bytes",
        )

    assert file_token == "media-token"
    assert requests[0].headers["authorization"] == "Bearer tenant-token"
    assert requests[0].url.path == "/open-apis/drive/v1/medias/upload_all"
    content = requests[0].content.decode("utf-8", errors="ignore")
    assert 'name="parent_type"' in content
    assert "ccm_import_open" in content
    assert 'name="parent_node"' in content
    assert '{"obj_type":"sheet","file_extension":"xlsx"}' in content


@pytest.mark.no_database_cleanup
@pytest.mark.asyncio(loop_scope="session")
async def test_create_import_task_sends_authorization_and_folder_mount_point() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {"ticket": "ticket-1"}})

    asset = Asset(
        type="file",
        module="mail",
        owner_type="admin_user",
        owner_id=1,
        original_name="assessment.xlsx",
        storage_key="hr-assets/mail/file/assessment.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=128,
        data={"suffix": "xlsx"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ticket = await feishu_preview._create_import_task(
            client,
            token="tenant-token",
            folder_token="folder-token",
            asset=asset,
            file_token="source-file-token",
        )

    assert ticket == "ticket-1"
    assert requests[0].headers["authorization"] == "Bearer tenant-token"
    assert requests[0].url.path == "/open-apis/drive/v1/import_tasks"
    assert requests[0].content
    payload = json.loads(requests[0].content)
    assert payload["file_extension"] == "xlsx"
    assert payload["file_token"] == "source-file-token"
    assert payload["type"] == "sheet"
    assert payload["point"] == {"mount_type": 1, "mount_key": "folder-token"}


@pytest.mark.no_database_cleanup
@pytest.mark.asyncio(loop_scope="session")
async def test_create_import_task_can_mount_to_app_managed_root() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {"ticket": "ticket-1"}})

    asset = Asset(
        type="file",
        module="mail",
        owner_type="admin_user",
        owner_id=1,
        original_name="assessment.xlsx",
        storage_key="hr-assets/mail/file/assessment.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=128,
        data={"suffix": "xlsx"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ticket = await feishu_preview._create_import_task(
            client,
            token="tenant-token",
            folder_token="",
            asset=asset,
            file_token="source-file-token",
        )

    assert ticket == "ticket-1"
    payload = json.loads(requests[0].content)
    assert payload["point"] == {"mount_type": 1, "mount_key": ""}


@pytest.mark.no_database_cleanup
@pytest.mark.asyncio(loop_scope="session")
async def test_publish_preview_public_link_sets_anyone_readable_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "permission_public": {
                        "external_access": True,
                        "link_share_entity": "anyone_readable",
                    }
                },
            },
        )

    monkeypatch.setattr(feishu_preview.settings, "FEISHU_PREVIEW_PUBLIC_LINK_ENABLED", True, raising=False)
    result = feishu_preview.FeishuPreviewResult(
        url="https://example.feishu.cn/sheets/sheet-token",
        document_token="sheet-token",
        document_type="sheet",
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        published = await feishu_preview._publish_public_preview_link(
            client,
            token="tenant-token",
            result=result,
        )

    assert published is True
    assert requests[0].headers["authorization"] == "Bearer tenant-token"
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/open-apis/drive/v1/permissions/sheet-token/public"
    assert requests[0].url.params["type"] == "sheet"
    payload = json.loads(requests[0].content)
    assert payload["external_access"] is True
    assert payload["link_share_entity"] == "anyone_readable"
    assert payload["security_entity"] == "anyone_can_view"


@pytest.mark.no_database_cleanup
def test_cached_preview_tracks_public_link_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = Asset(
        type="file",
        module="mail",
        owner_type="admin_user",
        owner_id=1,
        original_name="assessment.xlsx",
        storage_key="hr-assets/mail/file/assessment.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=128,
        data={},
    )
    signature = "signature-1"
    result = feishu_preview.FeishuPreviewResult(
        url="https://example.feishu.cn/sheets/sheet-token",
        document_token="sheet-token",
        document_type="sheet",
    )

    monkeypatch.setattr(feishu_preview.settings, "FEISHU_PREVIEW_PUBLIC_LINK_ENABLED", True, raising=False)
    feishu_preview._write_cached_preview(asset, signature, result, public_link_enabled=False)
    assert feishu_preview._cached_preview_has_required_public_link(asset, signature) is False

    feishu_preview._write_cached_preview(asset, signature, result, public_link_enabled=True)
    assert feishu_preview._cached_preview_has_required_public_link(asset, signature) is True
