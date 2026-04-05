import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from .model import Asset
from .schema import AssetRead, AssetUploadPayload


def get_asset_storage_root() -> Path:
    root = Path(settings.ASSET_STORAGE_DIR)
    if not root.is_absolute():
        root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_asset_file_path(asset: Asset) -> Path:
    return get_asset_storage_root() / asset.storage_key


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
        url=preview_url,
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
    content = await upload.read()
    if not content:
        raise BadRequestException("Uploaded file is empty.")

    suffix = Path(filename).suffix
    storage_key = f"{payload.module}/{payload.type}/{uuid4().hex}{suffix}"
    path = get_asset_storage_root() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    mime_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    asset = Asset(
        type=payload.type,
        module=payload.module,
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        original_name=filename,
        storage_key=storage_key,
        mime_type=mime_type,
        file_size=len(content),
        data={"suffix": suffix.lstrip(".").lower()},
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return serialize_asset(asset)


async def get_asset_preview(asset_id: int, db: AsyncSession) -> tuple[Asset, Path]:
    asset = await get_asset_model(asset_id, db)
    path = get_asset_file_path(asset)
    if not path.exists():
        raise NotFoundException("Asset file not found.")
    return asset, path


async def soft_delete_asset(asset_id: int, db: AsyncSession) -> dict[str, str]:
    asset = await get_asset_model(asset_id, db)
    asset.is_deleted = True
    asset.deleted_at = datetime.now(UTC)
    asset.updated_at = datetime.now(UTC)
    await db.flush()
    return {"message": "Asset deleted."}
