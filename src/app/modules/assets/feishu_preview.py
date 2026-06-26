from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from ...core.config import settings
from ...core.exceptions.http_exceptions import BadRequestException
from .model import Asset

FEISHU_PREVIEW_DATA_KEY = "feishu_spreadsheet_preview"
SUPPORTED_SPREADSHEET_EXTENSIONS = {"csv", "xls", "xlsx"}
FEISHU_MAX_IMPORT_BYTES = 20 * 1024 * 1024

_tenant_token_value = ""
_tenant_token_expires_at = 0.0


class FeishuPreviewResult(BaseModel):
    provider: str = "feishu"
    url: str
    document_token: str
    document_type: str = "sheet"
    cached: bool = False


def extract_feishu_folder_token(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if "://" not in cleaned and "/" not in cleaned:
        return cleaned

    parsed = urlparse(cleaned)
    path_parts = [part for part in parsed.path.split("/") if part]
    if "folder" in path_parts:
        folder_index = path_parts.index("folder")
        if folder_index + 1 < len(path_parts):
            return path_parts[folder_index + 1]
    return path_parts[-1] if path_parts else ""


def get_feishu_base_doc_url() -> str:
    parsed = urlparse(settings.FEISHU_PREVIEW_FOLDER_URL.strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://www.feishu.cn"


def get_asset_file_extension(asset: Asset) -> str:
    suffix = Path(asset.original_name or "").suffix.lower().lstrip(".")
    if suffix:
        return suffix
    data_suffix = str((asset.data or {}).get("suffix") or "").lower().lstrip(".")
    return data_suffix


def is_supported_spreadsheet_asset(asset: Asset) -> bool:
    return get_asset_file_extension(asset) in SUPPORTED_SPREADSHEET_EXTENSIONS


def build_asset_preview_signature(
    *,
    asset_id: int,
    storage_key: str,
    file_size: int,
    original_name: str,
    updated_at_iso: str,
) -> str:
    raw = f"{asset_id}|{storage_key}|{file_size}|{original_name}|{updated_at_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _asset_signature(asset: Asset) -> str:
    updated_at = asset.updated_at or asset.created_at
    updated_at_iso = updated_at.isoformat() if updated_at else ""
    return build_asset_preview_signature(
        asset_id=int(asset.id),
        storage_key=asset.storage_key,
        file_size=int(asset.file_size),
        original_name=asset.original_name,
        updated_at_iso=updated_at_iso,
    )


def _read_cached_preview(asset: Asset, signature: str) -> FeishuPreviewResult | None:
    cached = (asset.data or {}).get(FEISHU_PREVIEW_DATA_KEY)
    if not isinstance(cached, dict):
        return None
    if cached.get("signature") != signature:
        return None
    url = str(cached.get("url") or "")
    token = str(cached.get("document_token") or "")
    if not url or not token:
        return None
    return FeishuPreviewResult(
        url=url,
        document_token=token,
        document_type=str(cached.get("document_type") or "sheet"),
        cached=True,
    )


def _cached_preview_payload(asset: Asset, signature: str) -> dict[str, Any] | None:
    cached = (asset.data or {}).get(FEISHU_PREVIEW_DATA_KEY)
    if not isinstance(cached, dict):
        return None
    if cached.get("signature") != signature:
        return None
    return cached


def _cached_preview_has_required_public_link(asset: Asset, signature: str) -> bool:
    if not _public_link_enabled():
        return True
    cached = _cached_preview_payload(asset, signature)
    return bool(cached and cached.get("public_link_enabled") is True)


def _write_cached_preview(
    asset: Asset,
    signature: str,
    result: FeishuPreviewResult,
    *,
    public_link_enabled: bool = False,
) -> None:
    next_data = dict(asset.data or {})
    next_data[FEISHU_PREVIEW_DATA_KEY] = {
        "signature": signature,
        "url": result.url,
        "document_token": result.document_token,
        "document_type": result.document_type,
        "public_link_enabled": bool(public_link_enabled),
        "created_at": datetime.now(UTC).isoformat(),
    }
    asset.data = next_data


def _ensure_feishu_preview_configured(asset: Asset) -> str:
    if not is_supported_spreadsheet_asset(asset):
        raise BadRequestException("Feishu preview only supports spreadsheet attachments.")
    if not settings.FEISHU_PREVIEW_ENABLED:
        raise BadRequestException("Feishu spreadsheet preview is not enabled.")
    if not settings.FEISHU_APP_ID.strip():
        raise BadRequestException("FEISHU_APP_ID is not configured.")
    if not settings.FEISHU_APP_SECRET.get_secret_value().strip():
        raise BadRequestException("FEISHU_APP_SECRET is not configured.")

    return extract_feishu_folder_token(settings.FEISHU_PREVIEW_FOLDER_URL)


def _enforce_file_size(asset: Asset, content: bytes) -> None:
    configured_max_bytes = max(1, int(settings.FEISHU_PREVIEW_MAX_FILE_SIZE_MB)) * 1024 * 1024
    max_bytes = min(configured_max_bytes, FEISHU_MAX_IMPORT_BYTES)
    file_size = int(asset.file_size or len(content))
    if file_size > max_bytes or len(content) > max_bytes:
        raise BadRequestException("Spreadsheet is too large for Feishu online preview.")


def _open_api_url(path: str) -> str:
    base = settings.FEISHU_OPEN_API_BASE_URL.strip().rstrip("/") or "https://open.feishu.cn/open-apis"
    return f"{base}/{path.lstrip('/')}"


async def _post_feishu_json(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response = await client.post(_open_api_url(path), json=payload, headers=headers)
    return _parse_feishu_response(response, operation=operation or f"POST {path}")


async def _get_feishu_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    token: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response = await client.get(_open_api_url(path), headers=headers)
    return _parse_feishu_response(response, operation=operation or f"GET {path}")


async def _patch_feishu_json(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response = await client.patch(_open_api_url(path), json=payload, headers=headers)
    return _parse_feishu_response(response, operation=operation or f"PATCH {path}")


def _build_feishu_error_message(
    response: httpx.Response,
    payload: Any,
    *,
    operation: str | None,
) -> str:
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("msg") if isinstance(payload, dict) else response.text
    details = [f"status={response.status_code}"]
    if code not in (None, ""):
        details.append(f"code={code}")
    detail_suffix = f" ({', '.join(details)})"
    operation_text = f" during {operation}" if operation else ""
    return f"Feishu API request failed{operation_text}: {message or code or response.status_code}{detail_suffix}"


def _parse_feishu_response(response: httpx.Response, *, operation: str | None = None) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        operation_text = f" during {operation}" if operation else ""
        raise BadRequestException(f"Feishu API returned a non-JSON response{operation_text}.") from exc
    if response.status_code >= 400:
        raise BadRequestException(_build_feishu_error_message(response, payload, operation=operation))
    if isinstance(payload, dict) and int(payload.get("code") or 0) != 0:
        raise BadRequestException(_build_feishu_error_message(response, payload, operation=operation))
    return payload if isinstance(payload, dict) else {}


def _public_link_enabled() -> bool:
    return bool(settings.FEISHU_PREVIEW_PUBLIC_LINK_ENABLED)


async def _get_tenant_access_token(client: httpx.AsyncClient) -> str:
    global _tenant_token_expires_at, _tenant_token_value

    now = time.time()
    if _tenant_token_value and _tenant_token_expires_at - 60 > now:
        return _tenant_token_value

    payload = await _post_feishu_json(
        client,
        "/auth/v3/tenant_access_token/internal",
        {
            "app_id": settings.FEISHU_APP_ID.strip(),
            "app_secret": settings.FEISHU_APP_SECRET.get_secret_value().strip(),
        },
        operation="get tenant access token",
    )
    token = str(payload.get("tenant_access_token") or "")
    if not token:
        token = str((payload.get("data") or {}).get("tenant_access_token") or "")
    if not token:
        raise BadRequestException("Feishu tenant_access_token is missing in API response.")

    expire_seconds = int(payload.get("expire") or (payload.get("data") or {}).get("expire") or 7200)
    _tenant_token_value = token
    _tenant_token_expires_at = now + max(60, expire_seconds)
    return token


async def _upload_source_file(
    client: httpx.AsyncClient,
    *,
    token: str,
    folder_token: str,
    asset: Asset,
    content: bytes,
) -> str:
    response = await client.post(
        _open_api_url("/drive/v1/files/upload_all"),
        headers={"Authorization": f"Bearer {token}"},
        data={
            "file_name": asset.original_name,
            "parent_type": "explorer",
            "parent_node": folder_token,
            "size": str(len(content)),
        },
        files={"file": (asset.original_name, content, asset.mime_type)},
    )
    payload = _parse_feishu_response(response, operation="upload source file to Feishu folder")
    file_token = str((payload.get("data") or {}).get("file_token") or "")
    if not file_token:
        raise BadRequestException("Feishu upload response did not include file_token.")
    return file_token


async def _upload_import_source_media(
    client: httpx.AsyncClient,
    *,
    token: str,
    asset: Asset,
    content: bytes,
) -> str:
    response = await client.post(
        _open_api_url("/drive/v1/medias/upload_all"),
        headers={"Authorization": f"Bearer {token}"},
        data={
            "file_name": asset.original_name,
            "parent_type": "ccm_import_open",
            "parent_node": "",
            "size": str(len(content)),
            "extra": json.dumps(
                {"obj_type": "sheet", "file_extension": get_asset_file_extension(asset)},
                separators=(",", ":"),
            ),
        },
        files={"file": (asset.original_name, content, asset.mime_type)},
    )
    payload = _parse_feishu_response(response, operation="upload source media for Feishu import")
    file_token = str((payload.get("data") or {}).get("file_token") or "")
    if not file_token:
        raise BadRequestException("Feishu media upload response did not include file_token.")
    return file_token


async def _create_import_task(
    client: httpx.AsyncClient,
    *,
    token: str,
    folder_token: str,
    asset: Asset,
    file_token: str,
) -> str:
    payload = await _post_feishu_json(
        client,
        "/drive/v1/import_tasks",
        {
            "file_extension": get_asset_file_extension(asset),
            "file_token": file_token,
            "type": "sheet",
            "file_name": Path(asset.original_name).stem or asset.original_name,
            "point": {
                "mount_type": 1,
                "mount_key": folder_token,
            },
        },
        token=token,
        operation="create spreadsheet import task",
    )
    ticket = str((payload.get("data") or {}).get("ticket") or "")
    if not ticket:
        raise BadRequestException("Feishu import task response did not include ticket.")
    return ticket


def _walk_json(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, dict):
        for child in value.values():
            items.extend(_walk_json(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(_walk_json(child))
    return items


def _find_first_string(payload: dict[str, Any], keys: set[str]) -> str:
    for item in _walk_json(payload):
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _build_document_url(document_token: str) -> str:
    return f"{get_feishu_base_doc_url()}/sheets/{document_token}"


def _extract_document_token_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("sheets", "sheet"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return parts[-1] if parts else ""


def _extract_import_result(payload: dict[str, Any]) -> FeishuPreviewResult | None:
    url = _find_first_string(payload, {"url", "preview_url"})
    document_token = _find_first_string(payload, {"token", "document_token", "spreadsheet_token"})
    document_type = _find_first_string(payload, {"type", "document_type"}) or "sheet"
    if url and not document_token:
        document_token = _extract_document_token_from_url(url)
    if url or document_token:
        return FeishuPreviewResult(
            url=url or _build_document_url(document_token),
            document_token=document_token,
            document_type=document_type,
            cached=False,
        )

    status = _find_first_string(payload, {"status", "job_status", "task_status"}).lower()
    error_message = _find_first_string(payload, {"error_msg", "job_error_msg", "message"})
    if status and any(word in status for word in ("fail", "error")):
        raise BadRequestException(f"Feishu import task failed: {error_message or status}")
    return None


async def _poll_import_task(
    client: httpx.AsyncClient,
    *,
    token: str,
    ticket: str,
) -> FeishuPreviewResult:
    deadline = time.monotonic() + max(1.0, float(settings.FEISHU_PREVIEW_POLL_TIMEOUT_SECONDS))
    interval = max(0.2, float(settings.FEISHU_PREVIEW_POLL_INTERVAL_SECONDS))
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = await _get_feishu_json(
            client,
            f"/drive/v1/import_tasks/{ticket}",
            token=token,
            operation="poll spreadsheet import task",
        )
        last_payload = payload
        result = _extract_import_result(payload)
        if result:
            return result
        await asyncio.sleep(interval)
    raise BadRequestException(f"Feishu import task timed out: {last_payload.get('msg') or ticket}")


async def _publish_public_preview_link(
    client: httpx.AsyncClient,
    *,
    token: str,
    result: FeishuPreviewResult,
) -> bool:
    if not _public_link_enabled():
        return False
    if not result.document_token:
        return False

    document_type = (result.document_type or "sheet").strip() or "sheet"
    await _patch_feishu_json(
        client,
        f"/drive/v1/permissions/{result.document_token}/public?type={document_type}",
        {
            "external_access": True,
            "security_entity": "anyone_can_view",
            "comment_entity": "anyone_can_view",
            "share_entity": "anyone",
            "link_share_entity": "anyone_readable",
            "invite_external": True,
        },
        token=token,
        operation="enable public Feishu preview link",
    )
    return True


async def build_feishu_spreadsheet_preview(*, asset: Asset, content: bytes) -> FeishuPreviewResult:
    folder_token = _ensure_feishu_preview_configured(asset)
    _enforce_file_size(asset, content)

    signature = _asset_signature(asset)
    cached = _read_cached_preview(asset, signature)
    if cached and _cached_preview_has_required_public_link(asset, signature):
        return cached

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _get_tenant_access_token(client)
        if cached:
            public_link_enabled = False
            if not _cached_preview_has_required_public_link(asset, signature):
                public_link_enabled = await _publish_public_preview_link(client, token=token, result=cached)
                _write_cached_preview(asset, signature, cached, public_link_enabled=public_link_enabled)
            return cached

        if folder_token:
            source_file_token = await _upload_source_file(
                client,
                token=token,
                folder_token=folder_token,
                asset=asset,
                content=content,
            )
        else:
            source_file_token = await _upload_import_source_media(
                client,
                token=token,
                asset=asset,
                content=content,
            )
        ticket = await _create_import_task(
            client,
            token=token,
            folder_token=folder_token,
            asset=asset,
            file_token=source_file_token,
        )
        result = await _poll_import_task(client, token=token, ticket=ticket)
        public_link_enabled = await _publish_public_preview_link(client, token=token, result=result)

    _write_cached_preview(asset, signature, result, public_link_enabled=public_link_enabled)
    return result
