from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_current_admin_user
from .....core.db.database import async_get_db
from .....core.exceptions.http_exceptions import NotFoundException
from .....modules.admin.role.const import is_assessment_reviewer_only_permissions
from .....modules.job_progress.const import JobProgressDataKey
from .....modules.assets.schema import AssetRead, AssetUploadPayload
from .....modules.assets.service import build_asset_pdf_export, get_asset, get_asset_content, upload_asset

router = APIRouter(prefix="/assets", tags=["admin-assets"])


def _is_assessment_reviewer_only(current_admin: dict[str, Any]) -> bool:
    return is_assessment_reviewer_only_permissions(
        current_admin.get("permissions") or [],
        is_superuser=bool(current_admin.get("is_superuser")),
    )


async def current_admin_can_access_asset(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_admin: dict[str, Any],
) -> bool:
    if current_admin.get("is_superuser"):
        return True

    reviewer_only = _is_assessment_reviewer_only(current_admin)
    admin_user_id = int(current_admin["id"])
    owner_id = int(asset.get("owner_id") or 0)
    owner_type = asset.get("owner_type")
    if owner_type == "admin_user" and owner_id == admin_user_id:
        return True

    module = str(asset.get("module") or "")
    if module == "rich_text":
        return True
    if module == "company":
        return not reviewer_only
    if module == "mail":
        return False

    asset_id = int(asset.get("id") or 0)
    if asset_id <= 0:
        return False

    if module == "timesheet":
        if reviewer_only:
            return False
        timesheet_asset_result = await db.execute(
            text(
                """
                SELECT 1
                  FROM project_timesheet_record ptr
                WHERE ptr.is_deleted = 0
                  AND JSON_CONTAINS(ptr.data, :asset_id_json, '$.note_asset_ids')
                LIMIT 1
                """
            ),
            {"asset_id_json": str(asset_id)},
        )
        return timesheet_asset_result.first() is not None

    application_reviewer_filter = "AND jp.assessment_reviewer_admin_user_id = :admin_user_id" if reviewer_only else ""
    application_asset_result = await db.execute(
        text(
            f"""
            SELECT 1
            FROM candidate_application_field_value cav
            INNER JOIN candidate_application ca ON ca.id = cav.application_id
            INNER JOIN job j ON j.id = ca.job_id
            LEFT JOIN job_progress jp ON jp.application_id = ca.id AND jp.is_deleted = 0
            WHERE cav.asset_id = :asset_id
              AND ca.is_deleted = 0
              AND j.is_deleted = 0
              {application_reviewer_filter}
            LIMIT 1
            """
        ),
        {"asset_id": asset_id, "admin_user_id": admin_user_id},
    )
    if application_asset_result.first() is not None:
        return True

    process_asset_keys = (
        JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.CONTRACT_DRAFT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.SUBMITTED_CONTRACT_ATTACHMENT_ASSET_ID.value,
        JobProgressDataKey.CONTRACT_RETURN_ATTACHMENT_ASSET_ID.value,
    )
    process_conditions = " OR ".join(
        [
            f"CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(jp.data, '$.{key}')), 'null') AS SIGNED) = :asset_id"
            for key in process_asset_keys
        ]
    )
    process_reviewer_filter = "AND jp.assessment_reviewer_admin_user_id = :admin_user_id" if reviewer_only else ""
    process_asset_result = await db.execute(
        text(
            f"""
            SELECT 1
            FROM job_progress jp
            INNER JOIN job j ON j.id = jp.job_id
            WHERE jp.is_deleted = 0
              AND j.is_deleted = 0
              {process_reviewer_filter}
              AND ({process_conditions})
            LIMIT 1
            """
        ),
        {"asset_id": asset_id, "admin_user_id": admin_user_id},
    )
    if process_asset_result.first() is not None:
        return True

    if reviewer_only:
        return False

    contract_asset_result = await db.execute(
        text(
            """
            SELECT 1
            FROM contract_record cr
            INNER JOIN job j ON j.id = cr.job_id
            WHERE cr.is_deleted = 0
              AND j.is_deleted = 0
              AND (
                    cr.draft_contract_asset_id = :asset_id
                 OR cr.candidate_signed_contract_asset_id = :asset_id
                 OR cr.company_sealed_contract_asset_id = :asset_id
                 OR cr.contract_attachment_asset_id = :asset_id
              )
            LIMIT 1
            """
        ),
        {"asset_id": asset_id},
    )
    if contract_asset_result.first() is not None:
        return True

    company_logo_result = await db.execute(
        text(
            """
            SELECT 1
            FROM admin_company
            WHERE logo_asset_id = :asset_id
              AND is_deleted = 0
            LIMIT 1
            """
        ),
        {"asset_id": asset_id},
    )
    if company_logo_result.first() is not None:
        return True

    return False


async def ensure_current_admin_can_access_asset(
    db: AsyncSession,
    *,
    asset: dict[str, Any],
    current_admin: dict[str, Any],
) -> None:
    if not await current_admin_can_access_asset(db, asset=asset, current_admin=current_admin):
        raise NotFoundException("Asset not found.")


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
    if module in {"mail", "timesheet"}:
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
    response = Response(content=content, media_type=asset.mime_type)
    response.headers["Content-Disposition"] = build_content_disposition("inline", asset.original_name)
    return response


@router.get("/{asset_id}/download", dependencies=[Depends(get_current_admin_user)])
async def download_asset(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_payload = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
    asset, content = await get_asset_content(asset_id, db)
    response = Response(content=content, media_type=asset.mime_type)
    response.headers["Content-Disposition"] = build_content_disposition("attachment", asset.original_name)
    return response


@router.get("/{asset_id}/download-pdf", dependencies=[Depends(get_current_admin_user)])
async def download_asset_as_pdf(
    asset_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> Response:
    asset_payload = await get_asset(asset_id, db)
    await ensure_current_admin_can_access_asset(db, asset=asset_payload, current_admin=current_admin)
    asset, content = await get_asset_content(asset_id, db)
    pdf_bytes = build_asset_pdf_export(asset, content)
    filename = f"{asset.original_name.rsplit('.', 1)[0]}.pdf"
    response = Response(content=pdf_bytes, media_type="application/pdf")
    response.headers["Content-Disposition"] = build_content_disposition("attachment", filename)
    return response
