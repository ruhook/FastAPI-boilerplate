import asyncio
import logging
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import oss2
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from .content_policy import AssetContentPolicy, classify_asset_content
from .model import Asset
from .schema import AssetRead, AssetUploadPayload

DOCX_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
logger = logging.getLogger(__name__)


def _using_aliyun_oss() -> bool:
    return settings.ASSET_STORAGE_PROVIDER.strip().lower() == "aliyun_oss"


def _normalize_storage_filename(filename: str) -> str:
    normalized = (Path(filename).name or "asset").strip()
    normalized = normalized.replace("/", "_").replace("\\", "_")
    normalized = re.sub(r"[\x00-\x1f]", "", normalized)
    return normalized or "asset"


def _build_asset_storage_key(*, payload: AssetUploadPayload, original_name: str) -> str:
    filename = _normalize_storage_filename(original_name)
    date_path = datetime.now(UTC).strftime("%Y/%m/%d")
    prefix = settings.ASSET_STORAGE_KEY_PREFIX.strip("/")
    parts = [prefix, payload.module, payload.type, date_path, uuid4().hex, filename]
    return "/".join(part for part in parts if part)


def _get_oss_bucket() -> oss2.Bucket:
    endpoint = settings.ALIYUN_OSS_ENDPOINT.strip()
    access_key_id = settings.ALIYUN_OSS_ACCESS_KEY_ID.strip()
    access_key_secret = settings.ALIYUN_OSS_ACCESS_KEY_SECRET.get_secret_value().strip()
    bucket_name = settings.ALIYUN_OSS_BUCKET.strip()
    if not endpoint or not access_key_id or not access_key_secret or not bucket_name:
        raise RuntimeError("Aliyun OSS storage is not configured completely.")
    auth = oss2.Auth(access_key_id, access_key_secret)
    return oss2.Bucket(auth, endpoint, bucket_name)


def _build_public_asset_url(asset: Asset) -> str:
    return f"/api/v1/assets/{asset.id}/preview"


def get_asset_storage_root() -> Path:
    root = Path(settings.ASSET_STORAGE_DIR)
    if not root.is_absolute():
        root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_local_storage_path(storage_key: str) -> Path:
    root = get_asset_storage_root().resolve()
    path = (root / storage_key).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise RuntimeError("Asset storage key escapes the configured storage directory.") from None
    return path


def get_asset_file_path(asset: Asset) -> Path:
    if _using_aliyun_oss():
        raise RuntimeError("Local asset file path is unavailable when using Aliyun OSS storage.")
    return _get_local_storage_path(asset.storage_key)


def store_asset_content(*, storage_key: str, content: bytes, mime_type: str) -> None:
    if _using_aliyun_oss():
        bucket = _get_oss_bucket()
        bucket.put_object(
            storage_key,
            content,
            headers={
                "Content-Type": mime_type,
            },
        )
        return

    path = _get_local_storage_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def delete_asset_content(storage_key: str) -> None:
    if _using_aliyun_oss():
        _get_oss_bucket().delete_object(storage_key)
        return
    _get_local_storage_path(storage_key).unlink(missing_ok=True)


def read_asset_content(asset: Asset) -> bytes:
    if _using_aliyun_oss():
        bucket = _get_oss_bucket()
        return bytes(bucket.get_object(asset.storage_key).read())

    path = get_asset_file_path(asset)
    if not path.exists():
        raise NotFoundException("Asset file not found.")
    return path.read_bytes()


async def async_store_asset_content(*, storage_key: str, content: bytes, mime_type: str) -> None:
    await asyncio.to_thread(
        store_asset_content,
        storage_key=storage_key,
        content=content,
        mime_type=mime_type,
    )


async def async_delete_asset_content(storage_key: str) -> None:
    await asyncio.to_thread(delete_asset_content, storage_key)


async def async_read_asset_content(asset: Asset) -> bytes:
    return await asyncio.to_thread(read_asset_content, asset)


async def async_classify_asset_content(filename: str, content: bytes) -> AssetContentPolicy:
    return await asyncio.to_thread(classify_asset_content, filename, content)


async def read_upload_content_bounded(
    upload: UploadFile,
    *,
    max_bytes: int | None = None,
    chunk_bytes: int | None = None,
) -> bytes:
    resolved_max_bytes = max_bytes or settings.ASSET_MAX_UPLOAD_BYTES
    resolved_chunk_bytes = chunk_bytes or settings.ASSET_UPLOAD_CHUNK_BYTES
    if resolved_max_bytes <= 0 or resolved_chunk_bytes <= 0:
        raise RuntimeError("Asset upload limits must be positive integers.")

    content = bytearray()
    while True:
        chunk = await upload.read(resolved_chunk_bytes)
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > resolved_max_bytes:
            raise BadRequestException(f"Uploaded file is too large. Maximum size is {resolved_max_bytes} bytes.")


def serialize_asset(asset: Asset) -> dict[str, Any]:
    preview_url = f"/api/v1/assets/{asset.id}/preview"
    download_url = f"/api/v1/assets/{asset.id}/download"
    return AssetRead(
        id=asset.id,
        type=asset.type,
        module=asset.module,
        owner_type=asset.owner_type,
        owner_id=asset.owner_id,
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        file_size=asset.file_size,
        url=_build_public_asset_url(asset),
        preview_url=preview_url,
        download_url=download_url,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        data=asset.data or {},
    ).model_dump()


async def get_asset_model(asset_id: int, db: AsyncSession) -> Asset:
    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.is_deleted.is_(False),
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise NotFoundException("Asset not found.")
    return asset


async def get_asset(asset_id: int, db: AsyncSession) -> dict[str, Any]:
    asset = await get_asset_model(asset_id, db)
    return serialize_asset(asset)


async def ensure_assets_belong_to_owner(
    db: AsyncSession,
    *,
    owner_type: str,
    owner_id: int,
    asset_ids: list[int],
) -> list[Asset]:
    if not asset_ids:
        return []
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_(asset_ids),
            Asset.owner_type == owner_type,
            Asset.owner_id == owner_id,
            Asset.is_deleted.is_(False),
        )
    )
    assets = result.scalars().all()
    found_ids = {asset.id for asset in assets}
    missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise NotFoundException(f"Asset not found: {missing_ids[0]}")
    return list(assets)


async def ensure_assets_exist(
    db: AsyncSession,
    *,
    asset_ids: list[int],
) -> list[Asset]:
    if not asset_ids:
        return []
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_(asset_ids),
            Asset.is_deleted.is_(False),
        )
    )
    assets = result.scalars().all()
    found_ids = {asset.id for asset in assets}
    missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise NotFoundException(f"Asset not found: {missing_ids[0]}")
    return list(assets)


async def upload_asset(
    *,
    db: AsyncSession,
    payload: AssetUploadPayload,
    upload: UploadFile,
) -> dict[str, Any]:
    filename = (upload.filename or "").strip() or "asset"
    content = await read_upload_content_bounded(upload)
    if not content:
        raise BadRequestException("Uploaded file is empty.")

    policy = await async_classify_asset_content(filename, content)
    storage_key = _build_asset_storage_key(payload=payload, original_name=filename)
    await async_store_asset_content(storage_key=storage_key, content=content, mime_type=policy.mime_type)

    asset = Asset(
        type=payload.type,
        module=payload.module,
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        original_name=filename,
        storage_key=storage_key,
        mime_type=policy.mime_type,
        file_size=len(content),
        data={"suffix": policy.suffix},
    )
    try:
        db.add(asset)
        await db.flush()
        await db.refresh(asset)
    except Exception:
        try:
            await async_delete_asset_content(storage_key)
        except Exception:
            logger.exception("Failed to remove orphaned asset content after database failure.")
        raise
    return serialize_asset(asset)


async def create_asset_from_bytes(
    *,
    db: AsyncSession,
    payload: AssetUploadPayload,
    original_name: str,
    content: bytes,
    mime_type: str | None = None,
    data: dict[str, Any] | None = None,
) -> Asset:
    if not content:
        raise BadRequestException("Uploaded file is empty.")
    if len(content) > settings.ASSET_MAX_UPLOAD_BYTES:
        raise BadRequestException(
            f"Uploaded file is too large. Maximum size is {settings.ASSET_MAX_UPLOAD_BYTES} bytes."
        )
    resolved_name = (original_name or "").strip() or "asset"
    policy = await async_classify_asset_content(resolved_name, content)
    if mime_type is not None and mime_type.split(";", 1)[0].strip().lower() != policy.mime_type.split(
        ";", 1
    )[0]:
        raise BadRequestException("Declared asset media type does not match its content.")
    storage_key = _build_asset_storage_key(payload=payload, original_name=resolved_name)
    await async_store_asset_content(storage_key=storage_key, content=content, mime_type=policy.mime_type)
    asset_data = {"suffix": policy.suffix}
    if data:
        asset_data.update(data)

    asset = Asset(
        type=payload.type,
        module=payload.module,
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        original_name=resolved_name,
        storage_key=storage_key,
        mime_type=policy.mime_type,
        file_size=len(content),
        data=asset_data,
    )
    try:
        db.add(asset)
        await db.flush()
        await db.refresh(asset)
    except Exception:
        try:
            await async_delete_asset_content(storage_key)
        except Exception:
            logger.exception("Failed to remove orphaned asset content after database failure.")
        raise
    return asset


async def get_asset_content(asset_id: int, db: AsyncSession) -> tuple[Asset, bytes]:
    asset = await get_asset_model(asset_id, db)
    return asset, await async_read_asset_content(asset)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_lines(text: str, *, max_chars: int = 82) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", text).replace("\t", "    ").strip()
    if not normalized:
        return ["(No readable text was extracted from the original file.)"]

    wrapped: list[str] = []
    for paragraph in normalized.split("\n"):
        current = paragraph.strip()
        if not current:
            wrapped.append("")
            continue
        while len(current) > max_chars:
            split_at = current.rfind(" ", 0, max_chars + 1)
            if split_at <= 0:
                split_at = max_chars
            wrapped.append(current[:split_at].strip())
            current = current[split_at:].strip()
        wrapped.append(current)
    return wrapped


def _build_simple_pdf(lines: list[str]) -> bytes:
    cursor_y = 780
    stream_lines = ["BT /F1 13 Tf 50 780 Td"]
    for index, line in enumerate(lines):
        escaped = _escape_pdf_text(line)
        if index == 0:
            stream_lines.append(f"({escaped}) Tj")
        else:
            stream_lines.append("0 -18 Td")
            stream_lines.append(f"({escaped}) Tj")
        cursor_y -= 18
        if cursor_y <= 70:
            break
    stream_lines.append("ET")
    content_stream = " ".join(stream_lines).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        ),
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(content_stream)} >> stream\n".encode("ascii")
        + content_stream
        + b"\nendstream endobj\n",
    ]

    header = b"%PDF-1.4\n"
    body = bytearray(header)
    offsets = [0]
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_offset = len(body)
    body.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(body)


def _extract_docx_text(content: bytes) -> str:
    try:
        with ZipFile(BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError):
        return ""

    root = ElementTree.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", DOCX_NAMESPACE):
        fragments = [node.text or "" for node in paragraph.findall(".//w:t", DOCX_NAMESPACE)]
        paragraphs.append("".join(fragments).strip())
    return "\n".join(filter(None, paragraphs))


def build_asset_pdf_export(asset: Asset, content: bytes) -> bytes:
    suffix = Path(asset.original_name).suffix.lower()
    if suffix == ".pdf" or asset.mime_type == "application/pdf":
        return content

    extracted_text = ""
    if suffix == ".docx":
        extracted_text = _extract_docx_text(content)

    export_lines = [
        f"Exported PDF preview for: {asset.original_name}",
        f"Original mime type: {asset.mime_type}",
        "",
    ]
    if extracted_text:
        export_lines.extend(_wrap_pdf_lines(extracted_text))
    else:
        export_lines.extend(
            [
                "This file was exported as a simplified PDF preview.",
                "Direct text extraction is not available for the original format.",
                "Please keep the original attachment if you need layout-perfect content.",
            ]
        )
    return _build_simple_pdf(export_lines)


async def async_build_asset_pdf_export(asset: Asset, content: bytes) -> bytes:
    return await asyncio.to_thread(build_asset_pdf_export, asset, content)


async def soft_delete_asset(asset_id: int, db: AsyncSession) -> dict[str, str]:
    asset = await get_asset_model(asset_id, db)
    asset.is_deleted = True
    asset.deleted_at = datetime.now(UTC)
    asset.updated_at = datetime.now(UTC)
    await db.flush()
    return {"message": "Asset deleted."}
