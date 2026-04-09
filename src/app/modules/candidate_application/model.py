from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class CandidateApplication(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "candidate_application"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, index=True)
    form_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_form_template.id"),
        nullable=True,
        index=True,
    )
    job_snapshot_title: Mapped[str] = mapped_column(String(160), nullable=False)
    job_snapshot_company_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        index=True,
    )
