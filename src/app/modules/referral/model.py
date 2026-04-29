from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class ReferralRecord(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "referral_record"

    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True, unique=True)
    referred_talent_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("talent_profile.id"),
        nullable=True,
        index=True,
    )

    referrer_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referrer_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referred_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referred_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_referral_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    paid_reward_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    payout_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="tracking",
        server_default=text("'tracking'"),
        index=True,
    )
    last_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_paid_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )

