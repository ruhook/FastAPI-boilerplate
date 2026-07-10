from datetime import UTC, datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class CandidateApplication(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "candidate_application"
    __table_args__ = (
        Index(
            "uq_candidate_application_active_user_job",
            "user_id",
            "active_job_id",
            unique=True,
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, index=True)
    active_job_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN is_deleted = 0 THEN job_id ELSE NULL END", persisted=True),
        nullable=True,
    )
    form_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_form_template.id"),
        nullable=True,
        index=True,
    )
    job_snapshot_title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        index=True,
    )
