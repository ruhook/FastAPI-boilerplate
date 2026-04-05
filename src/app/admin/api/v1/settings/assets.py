from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user
from .....core.db.database import async_get_db
from .....modules.assets.schema import AssetRead, AssetUploadPayload
from .....modules.assets.service import get_asset, get_asset_preview, upload_asset

router = APIRouter(prefix="/assets", tags=["admin-assets"])


def build_content_disposition(disposition: str, filename: str) -> str:
    ascii_fallback = "".join(char if ord(char) < 128 else "_" for char in filename) or "download"
    encoded_filename = quote(filename, safe="")
    return f"{disposition}; filename={ascii_fallback!r}; filename*=UTF-8''{encoded_filename}"


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
    if module == "mail":
        owner_type = "admin_user"
        owner_id = int(current_admin["id"])
    payload = AssetUploadPayload(type=type, module=module, owner_type=owner_type, owner_id=owner_id)
    return await upload_asset(db=db, payload=payload, upload=file)


@router.get("/{asset_id}", response_model=AssetRead, dependencies=[Depends(get_current_admin_user)])
async def read_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict:
    asset = await get_asset(asset_id, db)
    if asset["module"] == "mail" and asset["owner_type"] == "admin_user" and asset["owner_id"] != int(current_admin["id"]):
        from .....core.exceptions.http_exceptions import NotFoundException
        raise NotFoundException("Asset not found.")
    return asset


@router.get("/{asset_id}/preview", dependencies=[Depends(get_current_admin_user)])
async def preview_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> FileResponse:
    asset, path = await get_asset_preview(asset_id, db)
    if asset.module == "mail" and asset.owner_type == "admin_user" and asset.owner_id != int(current_admin["id"]):
        from .....core.exceptions.http_exceptions import NotFoundException
        raise NotFoundException("Asset not found.")
    response = FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)
    response.headers["Content-Disposition"] = build_content_disposition("inline", asset.original_name)
    return response


@router.get("/{asset_id}/download", dependencies=[Depends(get_current_admin_user)])
async def download_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> FileResponse:
    asset, path = await get_asset_preview(asset_id, db)
    if asset.module == "mail" and asset.owner_type == "admin_user" and asset.owner_id != int(current_admin["id"]):
        from .....core.exceptions.http_exceptions import NotFoundException
        raise NotFoundException("Asset not found.")
    response = FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)
    response.headers["Content-Disposition"] = build_content_disposition("attachment", asset.original_name)
    return response
