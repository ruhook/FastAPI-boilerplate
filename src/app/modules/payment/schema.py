from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class BatchPayoutRequest(PayoutDetails):
    payable_ids: list[int] = Field(..., min_length=1)

    @field_validator("payable_ids")
    @classmethod
    def normalize_payable_ids(cls, value: list[int]) -> list[int]:
        ids = list(dict.fromkeys(value))
        if any(item < 1 for item in ids):
            raise ValueError("Payable IDs must be positive integers.")
        return ids

    def payout_details(self) -> PayoutDetails:
        return PayoutDetails(
            paid_at=self.paid_at,
            external_platform=self.external_platform,
            external_transaction_no=self.external_transaction_no,
            remark=self.remark,
        )


class PaymentReverseRequest(PayoutDetails):
    pass


class PaymentListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=1000)
    keyword: str | None = None
    payment_type: str | None = None
    user_id: int | None = Field(default=None, ge=1)
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

    @field_validator("keyword", "payment_type", mode="before")
    @classmethod
    def normalize_optional_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


class PaymentListPage(BaseModel):
    items: list[PaymentRead]
    total: int
    page: int
    page_size: int


class CandidatePaymentSummaryRead(BaseModel):
    total_paid: Decimal
    month_paid: Decimal
    referral_rewards_paid: Decimal
    latest_payment_at: datetime | None = None
    currency: str = "USD"
    month: str


class CandidatePaymentListPage(PaymentListPage):
    summary: CandidatePaymentSummaryRead
