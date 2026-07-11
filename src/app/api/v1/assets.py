from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...modules.assets.access_policy import ensure_current_user_can_access_asset_async
from ...modules.assets.responses import build_asset_response
from ...modules.assets.schema import AssetRead, AssetUploadPayload
from ...modules.assets.service import get_asset, get_asset_content, upload_asset
from ..dependencies import get_current_user

router = APIRouter(prefix="/assets", tags=["web-assets"])




@router.post("/upload", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def upload_user_asset(
    file: Annotated[UploadFile, File(...)],
    type: Annotated[str, Form(...)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    module: str = Form("general"),
) -> dict[str, Any]:
    return await upload_asset(
        db=db,
        payload=AssetUploadPayload(
            type=type,
            module=module,
            owner_type="user",
            owner_id=int(current_user["id"]),
        ),
        upload=file,
    )


@router.get("/{asset_id}", response_model=AssetRead)
async def read_user_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    asset = await get_asset(asset_id, db)
    await ensure_current_user_can_access_asset_async(
        db,
        asset=asset,
        current_user_id=int(current_user["id"]),
    )
    return asset


@router.get("/{asset_id}/preview")
async def preview_user_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> Response:
    asset, content = await get_asset_content(asset_id, db)
    await ensure_current_user_can_access_asset_async(
        db,
        asset={
            "id": asset.id,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
        },
        current_user_id=int(current_user["id"]),
    )
    return build_asset_response(asset, content, preview=True)


@router.get("/{asset_id}/download")
async def download_user_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> Response:
    asset, content = await get_asset_content(asset_id, db)
    await ensure_current_user_can_access_asset_async(
        db,
        asset={
            "id": asset.id,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
        },
        current_user_id=int(current_user["id"]),
    )
    return build_asset_response(asset, content, preview=False)
