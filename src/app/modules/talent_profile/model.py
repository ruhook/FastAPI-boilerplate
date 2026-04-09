from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class TalentProfile(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "talent_profile"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    nationality: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    education: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resume_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), nullable=True, index=True)
    latest_applied_job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"), nullable=True, index=True)
    latest_applied_job_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    latest_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_application_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_application.id"),
        nullable=True,
        index=True,
    )
    merge_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    last_merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
