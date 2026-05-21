from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class ProjectTimesheetRecord(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "project_timesheet_record"

    company_id: Mapped[int] = mapped_column(ForeignKey("admin_company.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("admin_company_project.id"), nullable=False, index=True)
    sub_project_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    talent_profile_id: Mapped[int | None] = mapped_column(ForeignKey("talent_profile.id"), nullable=True, index=True)
    contract_record_id: Mapped[int | None] = mapped_column(ForeignKey("contract_record.id"), nullable=True, index=True)
    user_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_email_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    team_leader_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)

    language: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    work_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    output_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    customer_human_efficiency_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    candidate_human_efficiency_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    customer_duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    candidate_duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    role_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    non_operational_duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    project_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    poc_evaluation: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
