from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..assets.schema import AssetRead
from ..assets.service import ensure_assets_belong_to_owner, get_asset
from .model import User


class CandidatePaymentSettingsRead(BaseModel):
    bank_card_number: str = ""
    id_attachment_asset_id: int | None = None
    id_attachment: AssetRead | None = None


class CandidatePaymentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_card_number: str | None = Field(default=None, max_length=64)
    id_attachment_asset_id: int | None = Field(default=None, ge=1)

    @field_validator("bank_card_number")
    @classmethod
    def normalize_bank_card_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized and not all(char.isdigit() or char in {" ", "-"} for char in normalized):
            raise ValueError("Bank card number can only contain digits, spaces, and hyphens.")
        return normalized


def _get_payment_info(user_data: dict[str, Any] | None) -> dict[str, Any]:
    payment_info = (user_data or {}).get("payment_info")
    return payment_info if isinstance(payment_info, dict) else {}


async def _get_current_user_model(user_id: int, db: AsyncSession) -> User:
    user = (
        await db.scalars(
            select(User).where(
                User.id == user_id,
                User.is_deleted.is_(False),
            )
        )
    ).one_or_none()
    if user is None:
        raise NotFoundException("User not found.")
    return user


async def _serialize_payment_settings(user: User, db: AsyncSession) -> CandidatePaymentSettingsRead:
    payment_info = _get_payment_info(user.data)
    id_attachment_asset_id = payment_info.get("id_attachment_asset_id")
    id_attachment = None
    if id_attachment_asset_id:
        try:
            id_attachment = AssetRead.model_validate(await get_asset(int(id_attachment_asset_id), db))
        except NotFoundException:
            id_attachment = None
    return CandidatePaymentSettingsRead(
        bank_card_number=str(payment_info.get("bank_card_number") or ""),
        id_attachment_asset_id=int(id_attachment_asset_id) if id_attachment_asset_id else None,
        id_attachment=id_attachment,
    )


async def get_candidate_payment_settings(*, user_id: int, db: AsyncSession) -> CandidatePaymentSettingsRead:
    return await _serialize_payment_settings(await _get_current_user_model(user_id, db), db)


async def update_candidate_payment_settings(
    *,
    user_id: int,
    payload: CandidatePaymentSettingsUpdate,
    db: AsyncSession,
) -> CandidatePaymentSettingsRead:
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
    next_payment_info = dict(_get_payment_info(user.data))
    if "bank_card_number" in payload.model_fields_set:
        next_payment_info["bank_card_number"] = payload.bank_card_number or ""
    if "id_attachment_asset_id" in payload.model_fields_set:
        current_asset_id = next_payment_info.get("id_attachment_asset_id")
        if current_asset_id not in (None, "", 0) and str(current_asset_id) != str(payload.id_attachment_asset_id):
            raise BadRequestException("ID document has already been submitted and cannot be changed.")
        next_payment_info["id_attachment_asset_id"] = payload.id_attachment_asset_id
    next_data["payment_info"] = next_payment_info
    user.data = next_data
    await db.flush()
    return await _serialize_payment_settings(user, db)
