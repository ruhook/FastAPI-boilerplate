from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class ContractRecord(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "contract_record"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    user_snapshot_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_snapshot_email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    talent_profile_id: Mapped[int | None] = mapped_column(ForeignKey("talent_profile.id"), nullable=True, index=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_application.id"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, index=True)
    job_progress_id: Mapped[int] = mapped_column(ForeignKey("job_progress.id"), nullable=False, index=True)
    job_snapshot_title: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    previous_contract_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("contract_record.id"),
        nullable=True,
        index=True,
    )

    service_customer_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_company.id"),
        nullable=True,
        index=True,
    )
    service_customer_project_id: Mapped[int] = mapped_column(
        ForeignKey("admin_company_project.id"),
        nullable=False,
        index=True,
    )

    agreement_ref_no: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    contract_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        default="Pending Activation",
        server_default=text("'Pending Activation'"),
    )
    contract_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        default="normal",
        server_default=text("'normal'"),
    )
    contractor_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    base_pay: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    legal_entity: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="T-Maxx International",
        server_default=text("'T-Maxx International'"),
    )
    worker_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Contractor",
        server_default=text("'Contractor'"),
    )
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    draft_contract_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), nullable=True, index=True)
    candidate_signed_contract_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset.id"),
        nullable=True,
        index=True,
    )
    company_sealed_contract_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset.id"),
        nullable=True,
        index=True,
    )
    contract_attachment_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset.id"),
        nullable=True,
        index=True,
    )

    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        default="pending",
        server_default=text("'pending'"),
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        server_default=text("1"),
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
