from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class ReferralBonusModel(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "referral_bonus_model"

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        index=True,
    )
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
    created_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )
    updated_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )


class UserReferralProfile(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "user_referral_profile"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, unique=True, index=True)
    referral_bonus_model_id: Mapped[int] = mapped_column(
        ForeignKey("referral_bonus_model.id"),
        nullable=False,
        index=True,
    )
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"), nullable=True, index=True)
    source_contract_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("contract_record.id"),
        nullable=True,
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
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("current_timestamp(0)"),
    )
    created_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )
    updated_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )

