from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .const import (
    DEFAULT_REFERRAL_BONUS_CAP,
    DEFAULT_REFERRAL_BONUS_CURRENCY,
    REFERRAL_BONUS_MODEL_STATUS_ACTIVE,
    normalize_referral_bonus_currency,
    normalize_referral_bonus_milestones,
    normalize_referral_bonus_status,
)


class ReferralBonusMilestone(BaseModel):
    required_hours: Decimal = Field(gt=0)
    reward_amount: Decimal = Field(gt=0)


class ReferralBonusModelBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    status: str = REFERRAL_BONUS_MODEL_STATUS_ACTIVE
    currency: str = DEFAULT_REFERRAL_BONUS_CURRENCY
    reward_cap: Decimal = Field(default=DEFAULT_REFERRAL_BONUS_CAP, ge=0)
    milestones: list[ReferralBonusMilestone] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty.")
        return normalized

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        try:
            return normalize_referral_bonus_status(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_referral_bonus_currency(value)

    @model_validator(mode="after")
    def validate_milestones(self) -> "ReferralBonusModelBase":
        raw_items = [
            {
                "required_hours": item.required_hours,
                "reward_amount": item.reward_amount,
            }
            for item in self.milestones
        ]
        self.milestones = [ReferralBonusMilestone(**item) for item in normalize_referral_bonus_milestones(raw_items)]
        if self.status == REFERRAL_BONUS_MODEL_STATUS_ACTIVE and not self.milestones:
            raise ValueError("Active referral bonus models require at least one milestone.")
        return self


class ReferralBonusModelCreate(ReferralBonusModelBase):
    model_config = ConfigDict(extra="forbid")


class ReferralBonusModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = None
    currency: str | None = None
    reward_cap: Decimal | None = Field(default=None, ge=0)
    milestones: list[ReferralBonusMilestone] | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty.")
        return normalized

    @field_validator("status")
    @classmethod
    def normalize_optional_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            return normalize_referral_bonus_status(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("currency")
    @classmethod
    def normalize_optional_currency(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_referral_bonus_currency(value)


class ReferralBonusModelRead(BaseModel):
    id: int
    name: str
    status: str
    currency: str
    reward_cap: Decimal
    milestones: list[ReferralBonusMilestone] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)
