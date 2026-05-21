from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..contract_record.model import ContractRecord
from ..job.model import Job
from ..referral.model import ReferralRecord
from .const import (
    DEFAULT_REFERRAL_BONUS_CAP,
    DEFAULT_REFERRAL_BONUS_CURRENCY,
    DEFAULT_REFERRAL_BONUS_MODEL_NAME,
    REFERRAL_BONUS_MODEL_STATUS_ACTIVE,
    calculate_referral_bonus_reward,
    default_referral_bonus_milestones_payload,
    normalize_referral_bonus_currency,
    normalize_referral_bonus_milestones,
    normalize_referral_bonus_status,
    quantize_bonus_decimal,
)
from .model import ReferralBonusModel, UserReferralProfile
from .schema import ReferralBonusModelCreate, ReferralBonusModelRead, ReferralBonusModelUpdate

REFERRAL_BONUS_MILESTONES_DATA_KEY = "milestones"


def _model_milestones(model: ReferralBonusModel | UserReferralProfile | ReferralRecord) -> list[dict[str, Any]]:
    data = model.data or {}
    milestones = data.get(REFERRAL_BONUS_MILESTONES_DATA_KEY)
    if isinstance(milestones, list):
        return normalize_referral_bonus_milestones(milestones)
    return default_referral_bonus_milestones_payload()


def serialize_referral_bonus_model(model: ReferralBonusModel) -> dict[str, Any]:
    return ReferralBonusModelRead(
        id=model.id,
        name=model.name,
        status=model.status,
        currency=model.currency,
        reward_cap=quantize_bonus_decimal(model.reward_cap),
        milestones=_model_milestones(model),
        created_at=model.created_at,
        updated_at=model.updated_at,
        data=model.data or {},
    ).model_dump()


async def list_referral_bonus_models(
    *,
    db: AsyncSession,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    conditions = [ReferralBonusModel.is_deleted.is_(False)]
    if not include_inactive:
        conditions.append(ReferralBonusModel.status == REFERRAL_BONUS_MODEL_STATUS_ACTIVE)
    result = await db.execute(
        select(ReferralBonusModel)
        .where(*conditions)
        .order_by(ReferralBonusModel.status.asc(), ReferralBonusModel.name.asc(), ReferralBonusModel.id.asc())
    )
    return [serialize_referral_bonus_model(model) for model in result.scalars().all()]


async def get_referral_bonus_model_model(
    model_id: int,
    db: AsyncSession,
    *,
    active_only: bool = False,
) -> ReferralBonusModel:
    conditions = [
        ReferralBonusModel.id == model_id,
        ReferralBonusModel.is_deleted.is_(False),
    ]
    if active_only:
        conditions.append(ReferralBonusModel.status == REFERRAL_BONUS_MODEL_STATUS_ACTIVE)
    result = await db.execute(select(ReferralBonusModel).where(*conditions))
    model = result.scalar_one_or_none()
    if model is None:
        raise NotFoundException("Referral bonus model not found.")
    return model


async def ensure_referral_bonus_model(
    *,
    model_id: int,
    db: AsyncSession,
    active_only: bool = True,
) -> ReferralBonusModel:
    return await get_referral_bonus_model_model(model_id, db, active_only=active_only)


def _payload_data_from_milestones(milestones: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        normalized = normalize_referral_bonus_milestones(milestones)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return {REFERRAL_BONUS_MILESTONES_DATA_KEY: normalized}


async def create_referral_bonus_model(
    payload: ReferralBonusModelCreate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    milestones = [
        {
            "required_hours": item.required_hours,
            "reward_amount": item.reward_amount,
        }
        for item in payload.milestones
    ]
    model = ReferralBonusModel(
        name=payload.name,
        status=normalize_referral_bonus_status(payload.status),
        currency=normalize_referral_bonus_currency(payload.currency),
        reward_cap=quantize_bonus_decimal(payload.reward_cap),
        created_by_admin_user_id=admin_user_id,
        updated_by_admin_user_id=admin_user_id,
        data=_payload_data_from_milestones(milestones),
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return serialize_referral_bonus_model(model)


async def update_referral_bonus_model(
    model_id: int,
    payload: ReferralBonusModelUpdate,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, Any]:
    model = await get_referral_bonus_model_model(model_id, db)
    if payload.name is not None:
        model.name = payload.name
    if payload.status is not None:
        model.status = normalize_referral_bonus_status(payload.status)
    if payload.currency is not None:
        model.currency = normalize_referral_bonus_currency(payload.currency)
    if payload.reward_cap is not None:
        model.reward_cap = quantize_bonus_decimal(payload.reward_cap)
    if payload.milestones is not None:
        model.data = _payload_data_from_milestones(
            [
                {
                    "required_hours": item.required_hours,
                    "reward_amount": item.reward_amount,
                }
                for item in payload.milestones
            ]
        )
    if model.status == REFERRAL_BONUS_MODEL_STATUS_ACTIVE and not _model_milestones(model):
        raise BadRequestException("Active referral bonus models require at least one milestone.")
    model.updated_by_admin_user_id = admin_user_id
    model.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(model)
    return serialize_referral_bonus_model(model)


async def delete_referral_bonus_model(
    model_id: int,
    db: AsyncSession,
    *,
    admin_user_id: int,
) -> dict[str, str]:
    model = await get_referral_bonus_model_model(model_id, db)
    usage_result = await db.execute(
        select(
            (
                select(func.count())
                .select_from(Job)
                .where(Job.referral_bonus_model_id == model.id, Job.is_deleted.is_(False))
                .scalar_subquery()
            ),
            (
                select(func.count())
                .select_from(UserReferralProfile)
                .where(
                    UserReferralProfile.referral_bonus_model_id == model.id,
                    UserReferralProfile.is_deleted.is_(False),
                )
                .scalar_subquery()
            ),
            (
                select(func.count())
                .select_from(ReferralRecord)
                .where(
                    ReferralRecord.referral_bonus_model_id == model.id,
                    ReferralRecord.is_deleted.is_(False),
                )
                .scalar_subquery()
            ),
        )
    )
    job_count, profile_count, referral_count = usage_result.one()
    if int(job_count or 0) or int(profile_count or 0) or int(referral_count or 0):
        raise BadRequestException("Referral bonus model is still in use.")
    model.is_deleted = True
    model.deleted_at = datetime.now(UTC)
    model.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return {"message": "Referral bonus model deleted."}


def build_referral_bonus_snapshot(model: ReferralBonusModel | UserReferralProfile | ReferralRecord) -> dict[str, Any]:
    model_id = getattr(model, "referral_bonus_model_id", None) or getattr(model, "id", None)
    model_name = getattr(model, "model_snapshot_name", None) or getattr(
        model,
        "name",
        DEFAULT_REFERRAL_BONUS_MODEL_NAME,
    )
    currency = normalize_referral_bonus_currency(getattr(model, "currency", DEFAULT_REFERRAL_BONUS_CURRENCY))
    reward_cap = quantize_bonus_decimal(getattr(model, "reward_cap", DEFAULT_REFERRAL_BONUS_CAP))
    return {
        "referral_bonus_model_id": int(model_id) if model_id is not None else None,
        "model_snapshot_name": model_name,
        "currency": currency,
        "reward_cap": str(reward_cap),
        "milestones": _model_milestones(model),
    }


async def get_user_referral_profile(
    *,
    user_id: int,
    db: AsyncSession,
) -> UserReferralProfile | None:
    result = await db.execute(
        select(UserReferralProfile).where(
            UserReferralProfile.user_id == user_id,
            UserReferralProfile.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def ensure_user_referral_profile_from_job(
    *,
    user_id: int,
    job: Job,
    db: AsyncSession,
    admin_user_id: int | None = None,
    contract_record: ContractRecord | None = None,
) -> UserReferralProfile:
    existing = await get_user_referral_profile(user_id=user_id, db=db)
    if existing is not None:
        return existing

    model = await ensure_referral_bonus_model(
        model_id=int(job.referral_bonus_model_id),
        db=db,
        active_only=False,
    )
    snapshot = build_referral_bonus_snapshot(model)
    profile = UserReferralProfile(
        user_id=user_id,
        referral_bonus_model_id=int(model.id),
        source_job_id=int(job.id),
        source_contract_record_id=int(contract_record.id) if contract_record is not None else None,
        model_snapshot_name=str(snapshot["model_snapshot_name"]),
        currency=str(snapshot["currency"]),
        reward_cap=quantize_bonus_decimal(snapshot["reward_cap"]),
        locked_at=datetime.now(UTC),
        created_by_admin_user_id=admin_user_id,
        updated_by_admin_user_id=admin_user_id,
        data={REFERRAL_BONUS_MILESTONES_DATA_KEY: snapshot["milestones"]},
    )
    db.add(profile)
    await db.flush()
    await db.refresh(profile)
    return profile


def calculate_referral_reward_from_record(
    record: ReferralRecord,
    work_hours: Decimal | int | float | str | None,
) -> Decimal:
    return calculate_referral_bonus_reward(
        work_hours,
        reward_cap=record.reward_cap,
        milestones=_model_milestones(record),
    )
