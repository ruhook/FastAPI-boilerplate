from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from .const import PAYMENT_TYPES, normalize_payment_type, quantize_money


class PaymentRecordRead(BaseModel):
    id: int
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: datetime
    external_platform: str | None = None
    external_transaction_no: str | None = None
    remark: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    company_id: int | None = None
    project_id: int | None = None
    company_name: str | None = None
    project_name: str | None = None
    contract_ref_no: str | None = None
    referral_referred_user_id: int | None = None
    referral_referred_name: str | None = None
    referral_referred_email: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PaymentRecordListPage(BaseModel):
    items: list[PaymentRecordRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PaymentPayableRecordRead(BaseModel):
    id: str
    source_key: str
    source_month: str
    payout_status: str
    payment_record_id: int | None = None
    user_id: int
    talent_profile_id: int | None = None
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    payment_type: str
    amount: Decimal
    currency: str = "USD"
    paid_at: datetime | None = None
    external_platform: str | None = None
    external_transaction_no: str | None = None
    remark: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    company_id: int | None = None
    project_id: int | None = None
    company_name: str | None = None
    project_name: str | None = None
    contract_ref_no: str | None = None
    country: str | None = None
    language: str | None = None
    work_hours: Decimal
    rate: Decimal | None = None
    bonus_multiplier: Decimal | None = None
    team_leader_base_pay: Decimal | None = None
    team_leader_bonus: Decimal | None = None
    source_record_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PaymentPayableSummaryRead(BaseModel):
    month: str
    pending_count: int = 0
    paid_count: int = 0
    pending_amount: Decimal = Decimal("0.00")
    paid_amount: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    currency: str = "USD"


class PaymentPayableListPage(BaseModel):
    items: list[PaymentPayableRecordRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    summary: PaymentPayableSummaryRead


class PaymentPayableMarkPaidRequest(BaseModel):
    source_keys: list[str] = Field(..., min_length=1)
    payout_status: str = "paid"
    paid_at: datetime | None = None
    external_platform: str | None = Field(default=None, max_length=120)
    external_transaction_no: str | None = Field(default=None, max_length=160)
    remark: str | None = None

    @field_validator("source_keys")
    @classmethod
    def normalize_source_keys(cls, value: list[str]) -> list[str]:
        keys = [str(item).strip() for item in value if str(item).strip()]
        if not keys:
            raise ValueError("At least one payable record is required.")
        return list(dict.fromkeys(keys))

    @field_validator("external_platform", "external_transaction_no", "remark", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PaymentPayableMarkPaidResponse(BaseModel):
    items: list[PaymentPayableRecordRead] = Field(default_factory=list)
    created_count: int


class PaymentPayableUpdateRequest(BaseModel):
    payout_status: str | None = None
    paid_at: datetime | None = None
    external_platform: str | None = Field(default=None, max_length=120)
    external_transaction_no: str | None = Field(default=None, max_length=160)
    remark: str | None = None

    @field_validator("external_platform", "external_transaction_no", "remark", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PaymentPayableUpdateResponse(BaseModel):
    item: PaymentPayableRecordRead


class CandidateEarningsSummaryRead(BaseModel):
    total_paid: Decimal
    month_paid: Decimal
    referral_rewards_paid: Decimal
    latest_payment_at: datetime | None = None
    currency: str = "USD"
    month: str


class CandidateEarningsRecordRead(BaseModel):
    id: int
    contract_record_id: int | None = None
    referral_record_id: int | None = None
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: datetime
    external_platform: str | None = None
    external_transaction_no: str | None = None
    company_id: int | None = None
    project_id: int | None = None
    company_name: str | None = None
    project_name: str | None = None
    contract_ref_no: str | None = None
    referral_referred_user_id: int | None = None
    referral_referred_name: str | None = None
    referral_referred_email: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class CandidateEarningsListPage(BaseModel):
    summary: CandidateEarningsSummaryRead
    items: list[CandidateEarningsRecordRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PaymentRecordCreateEntry(BaseModel):
    user_id: int = Field(..., ge=1)
    payment_type: str
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=1, max_length=8)
    paid_at: datetime | None = None
    contract_record_id: int | None = Field(default=None, ge=1)
    referral_record_id: int | None = Field(default=None, ge=1)
    external_platform: str | None = Field(default=None, max_length=120)
    external_transaction_no: str | None = Field(default=None, max_length=160)
    remark: str | None = None

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, value: str) -> str:
        return normalize_payment_type(value)

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return quantize_money(value)

    @field_validator("currency", "external_platform", "external_transaction_no", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PaymentRecordBatchCreateRequest(BaseModel):
    items: list[PaymentRecordCreateEntry] = Field(..., min_length=1)


class PaymentRecordBatchCreateResponse(BaseModel):
    items: list[PaymentRecordRead] = Field(default_factory=list)
    created_count: int


class PaymentRecordUserOptionRead(BaseModel):
    user_id: int
    talent_profile_id: int | None = None
    name: str
    email: str | None = None


class PaymentRecordContractOptionRead(BaseModel):
    contract_record_id: int
    user_id: int
    agreement_ref_no: str | None = None
    job_title: str | None = None
    company_id: int | None = None
    company_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    contract_status: str


class PaymentRecordReferralOptionRead(BaseModel):
    referral_record_id: int
    referrer_user_id: int
    referrer_name: str | None = None
    referrer_email: str | None = None
    referred_user_id: int
    referred_name: str | None = None
    referred_email: str | None = None
    payable_reward_amount: Decimal


class PaymentRecordOptionsRead(BaseModel):
    payment_types: list[str] = Field(default_factory=lambda: sorted(PAYMENT_TYPES))
    users: list[PaymentRecordUserOptionRead] = Field(default_factory=list)
    contracts: list[PaymentRecordContractOptionRead] = Field(default_factory=list)
    referrals: list[PaymentRecordReferralOptionRead] = Field(default_factory=list)
