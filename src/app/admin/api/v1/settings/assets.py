import asyncio
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .....core.config import settings
from .....core.db.database import async_get_db
from .....core.exceptions.http_exceptions import BadRequestException, NotFoundException
from .....modules.assets.responses import (
    build_asset_response,
    build_content_disposition,
    build_download_response,
)
from .....modules.assets.schema import AssetRead, AssetUploadPayload
from .....modules.assets.service import async_build_asset_pdf_export, get_asset, get_asset_content, upload_asset
from ...dependencies import get_current_admin_user

router = APIRouter(prefix="/assets", tags=["admin-assets"])


class AssetBatchDownloadZipPayload(BaseModel):
    asset_ids: list[int] = Field(default_factory=list)
    format: Literal["original", "pdf"] = "original"
    filename: str | None = None


async def current_admin_can_access_asset(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_admin: dict[str, Any],
) -> bool:
    _ = (db, asset)
    return current_admin.get("id") is not None


async def ensure_current_admin_can_access_asset(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_admin: dict[str, Any],
) -> None:
    if not await current_admin_can_access_asset(db, asset=asset, current_admin=current_admin):
        raise NotFoundException("Asset not found.")


def _safe_zip_member_name(filename: str, used_names: set[str]) -> str:
    cleaned = Path(filename.replace("\\", "/")).name.strip() or "attachment"
    if cleaned not in used_names:
        used_names.add(cleaned)
        return cleaned

    stem = Path(cleaned).stem or "attachment"
    suffix = Path(cleaned).suffix
    index = 2
    while True:
        candidate = f"{stem}-{index}{suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


@router.post("/upload", response_model=AssetRead, status_code=201, dependencies=[Depends(get_current_admin_user)])
async def upload_asset_endpoint(
    type: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    module: Annotated[str, Form()] = "general",
    owner_type: Annotated[str | None, Form()] = None,
    owner_id: Annotated[int | None, Form()] = None,
) -> dict:
    if module in {"mail", "timesheet"}:
        owner_type = "admin_user"
        owner_id = int(current_admin["id"])
    payload = AssetUploadPayload(type=type, module=module, owner_type=owner_type, owner_id=owner_id)
    return await upload_asset(db=db, payload=payload, upload=file)


@router.post("/batch-download-zip", dependencies=[Depends(get_current_admin_user)])
async def download_assets_as_zip(
    payload: AssetBatchDownloadZipPayload,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_ids = list(dict.fromkeys(asset_id for asset_id in payload.asset_ids if asset_id > 0))
    if not asset_ids:
        raise BadRequestException("No assets selected.")
    if len(asset_ids) > settings.ASSET_BATCH_MAX_FILES:
        raise BadRequestException("Too many assets selected.")

    output = SpooledTemporaryFile(max_size=settings.ASSET_ZIP_SPOOL_MAX_BYTES, mode="w+b")
    used_names: set[str] = set()
    total_content_bytes = 0
    archive = ZipFile(output, "w", ZIP_DEFLATED)
    archive_closed = False
    try:
        for asset_id in asset_ids:
            asset_payload = await get_asset(asset_id, db)
            await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
            asset, content = await get_asset_content(asset_id, db)
            if payload.format == "pdf":
                content = await async_build_asset_pdf_export(asset, content)
                filename = f"{asset.original_name.rsplit('.', 1)[0]}.pdf"
            else:
                filename = asset.original_name
            total_content_bytes += len(content)
            if total_content_bytes > settings.ASSET_BATCH_MAX_BYTES:
                raise BadRequestException("Selected assets are too large for one archive.")
            member_name = _safe_zip_member_name(filename, used_names)
            await asyncio.to_thread(archive.writestr, member_name, content)
        await asyncio.to_thread(archive.close)
        archive_closed = True
        await asyncio.to_thread(output.seek, 0)
    except Exception:
        if not archive_closed:
            try:
                await asyncio.to_thread(archive.close)
            except Exception:
                pass
        output.close()
        raise

    filename = (payload.filename or "attachments.zip").strip() or "attachments.zip"
    if not filename.lower().endswith(".zip"):
        filename = f"{filename}.zip"

    def iter_archive():
        try:
            while chunk := output.read(1024 * 1024):
                yield chunk
        finally:
            output.close()

    response = StreamingResponse(iter_archive(), media_type="application/zip")
    response.headers["Content-Disposition"] = build_content_disposition("attachment", filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@router.get("/{asset_id}", response_model=AssetRead, dependencies=[Depends(get_current_admin_user)])
async def read_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict:
    asset = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset, current_admin=current_admin)
    return asset


@router.get("/{asset_id}/preview", dependencies=[Depends(get_current_admin_user)])
async def preview_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_payload = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
    asset, content = await get_asset_content(asset_id, db)
    return build_asset_response(asset, content, preview=True)


@router.get("/{asset_id}/download", dependencies=[Depends(get_current_admin_user)])
async def download_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_payload = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
    asset, content = await get_asset_content(asset_id, db)
    return build_asset_response(asset, content, preview=False)


@router.get("/{asset_id}/download-pdf", dependencies=[Depends(get_current_admin_user)])
async def download_asset_as_pdf(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_payload = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
    asset, content = await get_asset_content(asset_id, db)
    pdf_bytes = await async_build_asset_pdf_export(asset, content)
    filename = f"{asset.original_name.rsplit('.', 1)[0]}.pdf"
    return build_download_response(pdf_bytes, media_type="application/pdf", filename=filename)
