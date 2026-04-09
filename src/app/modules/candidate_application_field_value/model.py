from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import StandardEntityMixin


class CandidateApplicationFieldValue(StandardEntityMixin, Base):
    __tablename__ = "candidate_application_field_value"

    application_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_application.id"),
        nullable=False,
        index=True,
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    field_label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[str] = mapped_column(String(50), nullable=False)
    catalog_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
