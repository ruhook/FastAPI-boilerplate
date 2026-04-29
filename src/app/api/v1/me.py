from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user
from ...core.exceptions.http_exceptions import NotFoundException
from ...core.db.database import async_get_db
from ...modules.assets.schema import AssetRead
from ...modules.assets.service import ensure_assets_belong_to_owner, get_asset
from ...modules.job_progress.schema import (
    CandidateContractListPage,
    CandidateJobApplicationDetailRead,
    CandidateJobApplicationListPage,
)
from ...modules.job_progress.service import (
    list_candidate_contracts,
    get_candidate_job_application_detail,
    list_candidate_job_applications,
)
from ...modules.project_timesheet_record.schema import CandidateTimesheetWorkspaceRead
from ...modules.project_timesheet_record.service import list_candidate_timesheet_workspace
from ...modules.payment_record.schema import CandidateEarningsListPage
from ...modules.payment_record.service import list_payment_records_for_candidate
from ...modules.referral.schema import CandidateReferralDashboardRead
from ...modules.referral.service import get_candidate_referral_dashboard
from ...modules.user.model import User

router = APIRouter(prefix="/me", tags=["web-me"])


class CandidatePaymentSettingsRead(BaseModel):
    bank_card_number: str = ""
    id_attachment_asset_id: int | None = None
    id_attachment: AssetRead | None = None


class CandidatePaymentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_card_number: str = Field(default="", max_length=64)
    id_attachment_asset_id: int | None = Field(default=None, ge=1)

    @field_validator("bank_card_number")
    @classmethod
    def normalize_bank_card_number(cls, value: str) -> str:
        normalized = value.strip()
        if normalized and not all(char.isdigit() or char in {" ", "-"} for char in normalized):
            raise ValueError("Bank card number can only contain digits, spaces, and hyphens.")
        return normalized


def _get_payment_info(user_data: dict[str, Any] | None) -> dict[str, Any]:
    payment_info = (user_data or {}).get("payment_info")
    return payment_info if isinstance(payment_info, dict) else {}


async def _serialize_payment_settings(user: User, db: AsyncSession) -> dict[str, Any]:
    payment_info = _get_payment_info(user.data)
    id_attachment_asset_id = payment_info.get("id_attachment_asset_id")
    id_attachment = None
    if id_attachment_asset_id:
        try:
            id_attachment = await get_asset(int(id_attachment_asset_id), db)
        except NotFoundException:
            id_attachment = None
    return CandidatePaymentSettingsRead(
        bank_card_number=str(payment_info.get("bank_card_number") or ""),
        id_attachment_asset_id=int(id_attachment_asset_id) if id_attachment_asset_id else None,
        id_attachment=id_attachment,
    ).model_dump()


async def _get_current_user_model(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.is_deleted.is_(False),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundException("User not found.")
    return user


@router.get("/applications", response_model=CandidateJobApplicationListPage)
async def read_my_applications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None),
    current_stage: str | None = Query(default=None),
    needs_action_only: bool = Query(default=False),
) -> dict:
    return await list_candidate_job_applications(
        user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        keyword=keyword,
        current_stage=current_stage,
        needs_action_only=needs_action_only,
        db=db,
    )


@router.get("/contracts", response_model=CandidateContractListPage)
async def read_my_contracts(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None),
) -> dict:
    return await list_candidate_contracts(
        user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        keyword=keyword,
        db=db,
    )


@router.get("/timesheets", response_model=CandidateTimesheetWorkspaceRead)
async def read_my_timesheets(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    bonus_month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> dict:
    return await list_candidate_timesheet_workspace(
        user_id=int(current_user["id"]),
        start_date=start_date,
        end_date=end_date,
        bonus_month=bonus_month,
        db=db,
    )


@router.get("/referrals", response_model=CandidateReferralDashboardRead)
async def read_my_referrals(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return await get_candidate_referral_dashboard(
        user_id=int(current_user["id"]),
        db=db,
    )


@router.get("/earnings", response_model=CandidateEarningsListPage)
async def read_my_earnings(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    payment_type: str | None = Query(default=None),
) -> dict:
    return await list_payment_records_for_candidate(
        db=db,
        user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        month=month,
        payment_type=payment_type,
    )


@router.get("/payment-settings", response_model=CandidatePaymentSettingsRead)
async def read_my_payment_settings(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    user = await _get_current_user_model(int(current_user["id"]), db)
    return await _serialize_payment_settings(user, db)


@router.patch("/payment-settings", response_model=CandidatePaymentSettingsRead)
async def update_my_payment_settings(
    payload: CandidatePaymentSettingsUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    user_id = int(current_user["id"])
    user = await _get_current_user_model(user_id, db)

    if payload.id_attachment_asset_id is not None:
        assets = await ensure_assets_belong_to_owner(
            db,
            owner_type="user",
            owner_id=user_id,
            asset_ids=[payload.id_attachment_asset_id],
        )
        asset = assets[0]
        if asset.module != "payment" or asset.type != "id_attachment":
            raise NotFoundException("Asset not found.")

    next_data = dict(user.data or {})
    next_data["payment_info"] = {
        "bank_card_number": payload.bank_card_number,
        "id_attachment_asset_id": payload.id_attachment_asset_id,
    }
    user.data = next_data
    await db.flush()
    return await _serialize_payment_settings(user, db)


@router.get("/applications/{application_id}", response_model=CandidateJobApplicationDetailRead)
async def read_my_application_detail(
    application_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return await get_candidate_job_application_detail(
        user_id=int(current_user["id"]),
        application_id=application_id,
        db=db,
    )
