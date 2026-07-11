from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .const import PayableStatus


@dataclass(frozen=True, slots=True)
class PayableDraft:
    source_key: str
    payment_type: str
    settlement_month: str
    user_id: int
    amount: Decimal
    currency: str = "USD"
    calculation_snapshot: dict[str, Any] = field(default_factory=dict)
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    company_id: int | None = None
    project_id: int | None = None
    user_snapshot_name: str | None = None
    user_snapshot_email: str | None = None
    company_snapshot_name: str | None = None
    project_snapshot_name: str | None = None
    contract_snapshot_ref_no: str | None = None
    referral_referred_user_id: int | None = None
    referral_referred_snapshot_name: str | None = None
    referral_referred_snapshot_email: str | None = None


class ManualPayableCreateRequest(BaseModel):
    payment_type: str = Field(..., min_length=1, max_length=32)
    settlement_month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    user_id: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=1, max_length=8)
    talent_profile_id: int | None = Field(default=None, ge=1)
    contract_record_id: int | None = Field(default=None, ge=1)
    referral_record_id: int | None = Field(default=None, ge=1)
    company_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    remark: str | None = None

    @field_validator("payment_type", "currency", "remark", mode="before")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


class PayableRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_key: str
    payment_type: str
    status: str
    settlement_month: str
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    company_id: int | None = None
    project_id: int | None = None
    amount: Decimal
    currency: str
    calculation_snapshot: dict[str, Any]
    user_snapshot_name: str | None = None
    user_snapshot_email: str | None = None
    company_snapshot_name: str | None = None
    project_snapshot_name: str | None = None
    contract_snapshot_ref_no: str | None = None
    referral_referred_user_id: int | None = None
    referral_referred_snapshot_name: str | None = None
    referral_referred_snapshot_email: str | None = None
    version: int
    processing_started_at: datetime | None = None
    paid_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PayableSummaryRead(BaseModel):
    pending_count: int = 0
    pending_amount: Decimal = Decimal("0.00")
    processing_count: int = 0
    processing_amount: Decimal = Decimal("0.00")
    paid_count: int = 0
    paid_amount: Decimal = Decimal("0.00")
    cancelled_count: int = 0
    cancelled_amount: Decimal = Decimal("0.00")
    reversed_count: int = 0
    reversed_amount: Decimal = Decimal("0.00")
    total_count: int = 0
    total_amount: Decimal = Decimal("0.00")


class PayableListPage(BaseModel):
    items: list[PayableRead]
    total: int
    page: int
    page_size: int
    summary: PayableSummaryRead


class PayableIdsRequest(BaseModel):
    payable_ids: list[int] = Field(..., min_length=1)

    @field_validator("payable_ids")
    @classmethod
    def normalize_payable_ids(cls, value: list[int]) -> list[int]:
        ids = list(dict.fromkeys(value))
        if any(item < 1 for item in ids):
            raise ValueError("Payable IDs must be positive integers.")
        return ids


class PayableBatchResponse(BaseModel):
    items: list[PayableRead]


class PayableSyncRequest(BaseModel):
    settlement_month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class PayableSyncResponse(BaseModel):
    settlement_month: str
    created_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    frozen_count: int = 0


class PayableListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=1000)
    settlement_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    payment_type: str | None = None
    status: PayableStatus | None = None
    keyword: str | None = None

    @field_validator("payment_type", "keyword", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None
