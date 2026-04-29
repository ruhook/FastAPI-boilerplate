from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin
from ..admin.admin_user.model import AdminUser  # noqa: F401


class PaymentRecord(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "payment_record"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    talent_profile_id: Mapped[int | None] = mapped_column(ForeignKey("talent_profile.id"), nullable=True, index=True)
    contract_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("contract_record.id"),
        nullable=True,
        index=True,
    )
    referral_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("referral_record.id"),
        nullable=True,
        index=True,
    )

    payment_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, server_default=text("0.00"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default=text("'USD'"))
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("current_timestamp(0)"),
        index=True,
    )

    external_platform: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_transaction_no: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    company_id: Mapped[int | None] = mapped_column(ForeignKey("admin_company.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("admin_company_project.id"), nullable=True, index=True)
    company_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    project_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    contract_snapshot_ref_no: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    referral_referred_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    referral_referred_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referral_referred_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

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
