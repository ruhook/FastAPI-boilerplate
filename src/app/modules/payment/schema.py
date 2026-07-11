from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class PayoutDetails(BaseModel):
    paid_at: datetime | None = None
    external_platform: str | None = None
    external_transaction_no: str | None = None
    remark: str | None = None

    @field_validator("external_platform", "external_transaction_no", "remark", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payable_id: int
    entry_type: str
    reversal_of_payment_id: int | None = None
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    company_id: int | None = None
    project_id: int | None = None
    referral_referred_user_id: int | None = None
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: datetime
    external_platform: str | None = None
    external_transaction_no: str | None = None
    remark: str | None = None
    user_snapshot_name: str | None = None
    user_snapshot_email: str | None = None
    company_snapshot_name: str | None = None
    project_snapshot_name: str | None = None
    contract_snapshot_ref_no: str | None = None
    referral_referred_snapshot_name: str | None = None
    referral_referred_snapshot_email: str | None = None
    created_at: datetime


class BatchPayoutItemResult(BaseModel):
    payable_id: int
    payment: PaymentRead | None = None
    error: str | None = None


class BatchPayoutResult(BaseModel):
    items: list[BatchPayoutItemResult]
    paid_count: int
    failed_count: int
