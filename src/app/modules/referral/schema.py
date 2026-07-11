from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ReferralMilestoneRead(BaseModel):
    required_hours: Decimal
    reward_amount: Decimal
    reached: bool = False


class ReferralRecordRead(BaseModel):
    id: int
    referrer_user_id: int
    referrer_name: str | None = None
    referrer_email: str | None = None
    referred_user_id: int
    referred_candidate: str
    referred_email: str | None = None
    onboarding_date: date | None = None
    status: str
    work_hours: Decimal
    referral_earnings: Decimal
    paid_reward_amount: Decimal
    payable_reward_amount: Decimal
    payout_status: str
    currency: str = "USD"
    reward_cap: Decimal = Decimal("0.00")
    bonus_model_name: str | None = None
    milestones: list[ReferralMilestoneRead] = Field(default_factory=list)
    last_paid_at: datetime | None = None


class CandidateReferralDashboardRead(BaseModel):
    eligible: bool = True
    ineligible_reason: str | None = None
    referral_code: str
    reward_cap: Decimal
    currency: str = "USD"
    bonus_model_name: str | None = None
    total_rewards: Decimal
    active_referral_count: int
    milestones: list[ReferralMilestoneRead] = Field(default_factory=list)
    items: list[ReferralRecordRead] = Field(default_factory=list)


class AdminReferralGroupRead(BaseModel):
    id: int
    referrer_user_id: int
    referrer_name: str | None = None
    referrer_email: str | None = None
    active_referral_count: int = 0
    total_rewards: Decimal = Decimal("0.00")
    paid_rewards: Decimal = Decimal("0.00")
    payable_rewards: Decimal = Decimal("0.00")
    last_paid_at: datetime | None = None
    children: list[ReferralRecordRead] = Field(default_factory=list)


class AdminReferralSummaryRead(BaseModel):
    active_referral_count: int = 0
    referrer_count: int = 0
    total_rewards: Decimal = Decimal("0.00")
    paid_rewards: Decimal = Decimal("0.00")
    payable_rewards: Decimal = Decimal("0.00")


class AdminReferralListPage(BaseModel):
    items: list[AdminReferralGroupRead]
    total: int
    page: int
    page_size: int
    summary: AdminReferralSummaryRead
    reward_cap: Decimal
    milestones: list[ReferralMilestoneRead] = Field(default_factory=list)
