from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, text
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
    referral_bonus_model_id: Mapped[int] = mapped_column(
        ForeignKey("referral_bonus_model.id"),
        nullable=False,
        index=True,
    )
    model_snapshot_name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="USD",
        server_default=text("'USD'"),
        index=True,
    )
    reward_cap: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
