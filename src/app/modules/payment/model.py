from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import StandardEntityMixin
from .const import PaymentEntryType


class Payment(StandardEntityMixin, Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint("payable_id", "entry_type", name="uq_payment_payable_entry_type"),
        UniqueConstraint("reversal_of_payment_id", name="uq_payment_reversal_of"),
    )

    payable_id: Mapped[int] = mapped_column(ForeignKey("payable.id"), nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PaymentEntryType.PAYMENT.value,
        server_default=text("'payment'"),
        index=True,
    )
    reversal_of_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payment.id"), nullable=True)

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
    company_id: Mapped[int | None] = mapped_column(ForeignKey("admin_company.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_company_project.id"),
        nullable=True,
        index=True,
    )
    referral_referred_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)

    payment_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default=text("'USD'"))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    external_platform: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_transaction_no: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    company_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    project_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    contract_snapshot_ref_no: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referral_referred_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    referral_referred_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )
