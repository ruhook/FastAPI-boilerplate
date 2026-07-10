import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse

from src.app.admin.api.v1.settings import assets as admin_assets_api
from src.app.api.v1.assets import ensure_current_user_can_access_asset
from src.app.core.config import settings
from src.app.core.exceptions.http_exceptions import BadRequestException, NotFoundException
from src.app.modules.assets import service as asset_service
from src.app.modules.assets.model import Asset
from src.app.modules.assets.schema import AssetUploadPayload
from src.app.modules.assets.service import (
    get_asset_file_path,
    read_upload_content_bounded,
    serialize_asset,
    upload_asset,
)

pytestmark = pytest.mark.no_database_cleanup


class ChunkedUpload:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self.offset >= len(self.content):
            return b""
        end = len(self.content) if size < 0 else min(len(self.content), self.offset + size)
        chunk = self.content[self.offset : end]
        self.offset = end
        return chunk


def build_asset() -> Asset:
    asset = Asset(
        id=9,
        type="file",
        module="job_progress",
        owner_type="user",
        owner_id=7,
        original_name="resume.pdf",
        storage_key="private/resume.pdf",
        mime_type="application/pdf",
        file_size=10,
        data={},
    )
    asset.created_at = datetime.now(UTC)
    return asset


def test_asset_serialization_never_exposes_permanent_oss_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ASSET_STORAGE_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(settings, "ALIYUN_OSS_ENDPOINT", "https://oss.example.com")
    monkeypatch.setattr(settings, "ALIYUN_OSS_BUCKET_NON_PRODUCTION", "public-bucket")

    serialized = serialize_asset(build_asset())

    assert serialized["url"] == "/api/v1/assets/9/preview"
    assert "oss.example.com" not in repr(serialized)
    assert "private/resume.pdf" not in repr(serialized)


def test_candidate_cannot_access_another_candidates_owned_asset() -> None:
    with pytest.raises(NotFoundException, match="not found"):
        ensure_current_user_can_access_asset(
            {"owner_type": "user", "owner_id": 7},
            current_user_id=8,
        )


@pytest.mark.asyncio
async def test_authenticated_admin_can_access_asset_created_by_another_admin() -> None:
    allowed = await admin_assets_api.current_admin_can_access_asset(
        SimpleNamespace(),  # type: ignore[arg-type]
        asset={"id": 9, "module": "mail", "owner_type": "admin_user", "owner_id": 7},
        current_admin={"id": 8, "is_superuser": False, "permissions": []},
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_upload_is_read_in_bounded_chunks() -> None:
    upload = ChunkedUpload(b"abcdefgh")

    content = await read_upload_content_bounded(
        upload,  # type: ignore[arg-type]
        max_bytes=8,
        chunk_bytes=3,
    )

    assert content == b"abcdefgh"
    assert upload.read_sizes == [3, 3, 3, 3]


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected_before_reading_rest() -> None:
    upload = ChunkedUpload(b"abcdefghij")

    with pytest.raises(BadRequestException, match="too large"):
        await read_upload_content_bounded(
            upload,  # type: ignore[arg-type]
            max_bytes=5,
            chunk_bytes=3,
        )

    assert upload.offset == 6


def test_local_storage_path_cannot_escape_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "ASSET_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "ASSET_STORAGE_DIR", str(tmp_path))
    asset = build_asset()
    asset.storage_key = "../../outside.pdf"

    with pytest.raises(RuntimeError, match="escapes"):
        get_asset_file_path(asset)


@pytest.mark.asyncio
async def test_database_failure_removes_uploaded_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_keys: list[str] = []
    deleted_keys: list[str] = []

    def fake_store(*, storage_key: str, **_kwargs) -> None:
        stored_keys.append(storage_key)

    def fake_delete(storage_key: str) -> None:
        deleted_keys.append(storage_key)

    class FailingDatabase:
        def add(self, _asset: Asset) -> None:
            return None

        async def flush(self) -> None:
            raise RuntimeError("database failed")

    upload = ChunkedUpload(b"%PDF-1.7\nasset")
    upload.filename = "resume.pdf"
    upload.content_type = "application/pdf"
    monkeypatch.setattr(asset_service, "store_asset_content", fake_store)
    monkeypatch.setattr(asset_service, "delete_asset_content", fake_delete)

    with pytest.raises(RuntimeError, match="database failed"):
        await upload_asset(
            db=FailingDatabase(),  # type: ignore[arg-type]
            payload=AssetUploadPayload(type="file", module="candidate"),
            upload=upload,  # type: ignore[arg-type]
        )

    assert deleted_keys == stored_keys


@pytest.mark.asyncio
async def test_asset_storage_helpers_offload_sync_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    async def fake_to_thread(function, *args, **kwargs):
        calls.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(asset_service, "store_asset_content", lambda **kwargs: None)
    monkeypatch.setattr(asset_service, "delete_asset_content", lambda storage_key: None)
    monkeypatch.setattr(asset_service, "read_asset_content", lambda asset: b"content")

    async_store = getattr(asset_service, "async_store_asset_content", None)
    async_delete = getattr(asset_service, "async_delete_asset_content", None)
    async_read = getattr(asset_service, "async_read_asset_content", None)
    assert async_store is not None
    assert async_delete is not None
    assert async_read is not None

    await async_store(storage_key="key", content=b"x", mime_type="text/plain")
    await async_delete("key")
    assert await async_read(build_asset()) == b"content"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_batch_zip_uses_configured_file_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ASSET_BATCH_MAX_FILES", 1)

    with pytest.raises(BadRequestException, match="Too many"):
        await admin_assets_api.download_assets_as_zip(
            payload=admin_assets_api.AssetBatchDownloadZipPayload(asset_ids=[1, 2]),
            db=SimpleNamespace(),  # type: ignore[arg-type]
            current_admin={"id": 1, "is_superuser": True},
        )


@pytest.mark.asyncio
async def test_batch_zip_rejects_aggregate_content_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = build_asset()

    async def fake_get_asset(_asset_id: int, _db) -> dict:
        return serialize_asset(asset)

    async def fake_get_content(_asset_id: int, _db) -> tuple[Asset, bytes]:
        return asset, b"four"

    async def allow_access(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(settings, "ASSET_BATCH_MAX_BYTES", 3)
    monkeypatch.setattr(admin_assets_api, "get_asset", fake_get_asset)
    monkeypatch.setattr(admin_assets_api, "get_asset_content", fake_get_content)
    monkeypatch.setattr(admin_assets_api, "ensure_current_admin_can_access_asset", allow_access)

    with pytest.raises(BadRequestException, match="too large"):
        await admin_assets_api.download_assets_as_zip(
            payload=admin_assets_api.AssetBatchDownloadZipPayload(asset_ids=[1]),
            db=SimpleNamespace(),  # type: ignore[arg-type]
            current_admin={"id": 1, "is_superuser": True},
        )


@pytest.mark.asyncio
async def test_batch_zip_offloads_pdf_conversion_and_archive_write(monkeypatch: pytest.MonkeyPatch) -> None:
    offloaded_names: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(function, *args, **kwargs):
        offloaded_names.append(getattr(function, "__name__", function.__class__.__name__))
        return await real_to_thread(function, *args, **kwargs)

    asset = build_asset()

    async def fake_get_asset(_asset_id: int, _db) -> dict:
        return serialize_asset(asset)

    async def fake_get_content(_asset_id: int, _db) -> tuple[Asset, bytes]:
        return asset, b"%PDF-1.7\nasset"

    async def allow_access(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)
    monkeypatch.setattr(admin_assets_api, "get_asset", fake_get_asset)
    monkeypatch.setattr(admin_assets_api, "get_asset_content", fake_get_content)
    monkeypatch.setattr(admin_assets_api, "ensure_current_admin_can_access_asset", allow_access)

    response = await admin_assets_api.download_assets_as_zip(
        payload=admin_assets_api.AssetBatchDownloadZipPayload(asset_ids=[asset.id], format="pdf"),
        db=SimpleNamespace(),  # type: ignore[arg-type]
        current_admin={"id": 1, "is_superuser": True},
    )
    assert isinstance(response, StreamingResponse)
    assert "build_asset_pdf_export" in offloaded_names
    assert "writestr" in offloaded_names
    assert "close" in offloaded_names
