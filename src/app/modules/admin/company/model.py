from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class AdminCompany(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "admin_company"

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), nullable=True, index=True)


class AdminCompanyProject(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "admin_company_project"
    __table_args__ = (
        UniqueConstraint("company_id", "name", "is_deleted", name="uq_admin_company_project_company_name_active"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("admin_company.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
