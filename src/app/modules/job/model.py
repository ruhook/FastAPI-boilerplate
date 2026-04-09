from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class Job(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "job"

    title: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="DA")
    country: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    work_mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    compensation_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    compensation_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    compensation_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="Per Hour")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    applicant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    owner_admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=False,
        index=True,
    )
    form_template_id: Mapped[int] = mapped_column(
        ForeignKey("admin_form_template.id"),
        nullable=False,
        index=True,
    )

    assessment_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    assessment_mail_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_account.id"),
        nullable=True,
        index=True,
        default=None,
    )
    assessment_mail_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_template.id"),
        nullable=True,
        index=True,
        default=None,
    )
    assessment_mail_signature_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_signature.id"),
        nullable=True,
        index=True,
        default=None,
    )
