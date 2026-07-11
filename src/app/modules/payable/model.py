from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import StandardEntityMixin
from .const import PayableStatus


class Payable(StandardEntityMixin, Base):
    __tablename__ = "payable"

    source_key: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    payment_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PayableStatus.PENDING.value,
        server_default=text("'pending'"),
        index=True,
    )
    settlement_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)

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

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default=text("'USD'"))
    calculation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    user_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    company_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    project_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    contract_snapshot_ref_no: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    referral_referred_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    referral_referred_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    referral_referred_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    __mapper_args__ = {"version_id_col": version}


class PayableTimesheetSource(StandardEntityMixin, Base):
    __tablename__ = "payable_timesheet_source"
    __table_args__ = (
        UniqueConstraint(
            "payable_id",
            "project_timesheet_record_id",
            name="uq_payable_timesheet_source",
        ),
    )

    payable_id: Mapped[int] = mapped_column(ForeignKey("payable.id"), nullable=False, index=True)
    project_timesheet_record_id: Mapped[int] = mapped_column(
        ForeignKey("project_timesheet_record.id"),
        nullable=False,
        index=True,
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    work_hours_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount_contribution_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
