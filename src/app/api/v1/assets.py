from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import NotFoundException
from ...modules.job_progress.const import JobProgressDataKey
from ...modules.assets.schema import AssetRead, AssetUploadPayload
from ...modules.assets.service import get_asset, get_asset_preview, upload_asset

router = APIRouter(prefix="/assets", tags=["web-assets"])


def build_content_disposition(disposition: str, filename: str) -> str:
    ascii_fallback = "".join(char if ord(char) < 128 else "_" for char in filename) or "download"
    encoded_filename = quote(filename, safe="")
    return f"{disposition}; filename={ascii_fallback!r}; filename*=UTF-8''{encoded_filename}"


def ensure_current_user_can_access_asset(asset: dict[str, Any], current_user_id: int) -> None:
    if asset.get("owner_type") != "user" or int(asset.get("owner_id") or 0) != current_user_id:
        raise NotFoundException("Asset not found.")


async def current_user_can_access_asset(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_user_id: int,
) -> bool:
    if asset.get("owner_type") == "user" and int(asset.get("owner_id") or 0) == current_user_id:
        return True

    asset_id = int(asset.get("id") or 0)
    if asset_id <= 0:
        return False

    application_asset_result = await db.execute(
        text(
            """
            SELECT 1
            FROM candidate_application_field_value cav
            INNER JOIN candidate_application ca ON ca.id = cav.application_id
            WHERE cav.asset_id = :asset_id
              AND ca.user_id = :user_id
            LIMIT 1
            """
        ),
        {"asset_id": asset_id, "user_id": current_user_id},
    )
    if application_asset_result.first() is not None:
        return True

    process_asset_keys = (
        JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID.value,
    )
    conditions = " OR ".join(
        [
            f"CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(jp.data, '$.{key}')), 'null') AS SIGNED) = :asset_id"
            for key in process_asset_keys
        ]
    )
    process_asset_result = await db.execute(
        text(
            f"""
            SELECT 1
            FROM job_progress jp
            WHERE jp.user_id = :user_id
              AND jp.is_deleted = 0
              AND ({conditions})
            LIMIT 1
            """
        ),
        {"asset_id": asset_id, "user_id": current_user_id},
    )
    return process_asset_result.first() is not None


async def ensure_current_user_can_access_asset_async(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_user_id: int,
) -> None:
    if not await current_user_can_access_asset(db, asset=asset, current_user_id=current_user_id):
        raise NotFoundException("Asset not found.")


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
) -> FileResponse:
    asset, path = await get_asset_preview(asset_id, db)
    await ensure_current_user_can_access_asset_async(
        db,
        asset={
            "id": asset.id,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
        },
        current_user_id=int(current_user["id"]),
    )
    response = FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)
    response.headers["Content-Disposition"] = build_content_disposition("inline", asset.original_name)
    return response


@router.get("/{asset_id}/download")
async def download_user_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> FileResponse:
    asset, path = await get_asset_preview(asset_id, db)
    await ensure_current_user_can_access_asset_async(
        db,
        asset={
            "id": asset.id,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
        },
        current_user_id=int(current_user["id"]),
    )
    response = FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)
    response.headers["Content-Disposition"] = build_content_disposition("attachment", asset.original_name)
    return response
