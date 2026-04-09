from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class JobProgress(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "job_progress"

    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_application.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    talent_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("talent_profile.id"),
        nullable=True,
        index=True,
    )
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    screening_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    assessment_reviewer_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
        default=None,
    )
    assessment_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    entered_stage_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        index=True,
    )
